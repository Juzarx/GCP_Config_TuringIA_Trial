"""
Cloud Function que se activa al subir un archivo a Cloud Storage, extrae sus
metadatos y los envía firmados a un webhook de Google Apps Script.

Decisiones técnicas:
- Trigger `finalized`: solo se dispara cuando el objeto terminó de subirse.
- Autenticación del webhook con HMAC-SHA256: la firma se calcula sobre el
  texto exacto del payload y viaja en el cuerpo (Apps Script no puede leer
  encabezados HTTP).
- El secreto llega desde Secret Manager como variable de entorno; nunca está
  en el código.
- Timestamp en milisegundos dentro del payload: el receptor rechaza mensajes
  viejos (protección contra replay).
- Apps Script siempre responde HTTP 200; el resultado real va en el JSON
  (`ok`, `codigo`), por eso se interpreta el cuerpo.
- Reintentos: los fallos transitorios (red, error 500) se relanzan para que
  Eventarc reintente. Los rechazos por datos o firma inválidos no se
  reintentan, porque repetirlos no los arreglaría.
"""
import hashlib
import hmac
import json
import os
import time

import functions_framework
import requests

TIMEOUT_SEGUNDOS = 20


def log(severity, message, **campos):
    """Escribe un log estructurado que Cloud Logging reconoce."""
    print(json.dumps({"severity": severity, "message": message, **campos}))


def extraer_metadatos(data):
    """
    Extrae nombre, tamaño y tipo del evento de Storage.
    Lanza ValueError si el evento no trae los datos esperados.
    """
    if not isinstance(data, dict):
        raise ValueError("El evento no contiene un diccionario de datos")

    nombre = data.get("name")
    bucket = data.get("bucket")
    if not nombre or not bucket:
        raise ValueError("Faltan campos obligatorios: 'name' o 'bucket'")

    return {
        "bucket": bucket,
        "nombre": nombre,
        # Storage envía el tamaño como string, se convierte a entero
        "tamano_bytes": int(data.get("size", 0)),
        "tipo_contenido": data.get("contentType", "desconocido"),
        "clase_almacenamiento": data.get("storageClass", "desconocida"),
        "fecha_creacion": data.get("timeCreated", "desconocida"),
    }


def construir_payload(metadatos, id_evento):
    """Arma el texto JSON que se firmará y se enviará."""
    return json.dumps({
        **metadatos,
        "id_evento": id_evento,
        "timestamp": int(time.time() * 1000),  # milisegundos
    })


def firmar(payload, secreto):
    """HMAC-SHA256 en hexadecimal, igual que la función firmar() de Apps Script."""
    return hmac.new(
        secreto.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def enviar_webhook(payload, secreto, url):
    """
    Envía el payload firmado. Devuelve (respuesta_json, milisegundos).
    requests sigue la redirección 302 que hace Apps Script y lee la respuesta.
    """
    sobre = {"payload": payload, "signature": firmar(payload, secreto)}
    inicio = time.monotonic()
    respuesta = requests.post(url, json=sobre, timeout=TIMEOUT_SEGUNDOS)
    milisegundos = int((time.monotonic() - inicio) * 1000)
    respuesta.raise_for_status()
    return respuesta.json(), milisegundos


@functions_framework.cloud_event
def procesar_archivo(cloud_event):
    """Punto de entrada: se ejecuta por cada archivo subido al bucket."""
    id_evento = cloud_event["id"]
    log("INFO", "Evento recibido", id_evento=id_evento)

    # 1) Validación del evento
    try:
        metadatos = extraer_metadatos(cloud_event.data)
    except (ValueError, TypeError) as error:
        # Evento inválido: reintentar no ayudaría
        log("ERROR", f"Evento con datos inválidos: {error}", id_evento=id_evento)
        return

    # 2) Configuración (viene de variables de entorno y Secret Manager)
    url = os.environ.get("WEBHOOK_URL")
    secreto = os.environ.get("WEBHOOK_SECRET")
    if not url or not secreto:
        log("CRITICAL", "Falta WEBHOOK_URL o WEBHOOK_SECRET", id_evento=id_evento)
        raise RuntimeError("Función sin configurar")

    # 3) Envío al webhook
    try:
        payload = construir_payload(metadatos, id_evento)
        resultado, ms = enviar_webhook(payload, secreto, url)
    except (requests.RequestException, ValueError) as error:
        # Fallo de red, timeout o respuesta ilegible: se relanza para reintento
        log("ERROR", f"Fallo al llamar al webhook: {error}",
            id_evento=id_evento, tipo_error=type(error).__name__)
        raise

    # 4) Interpretar la respuesta del receptor
    codigo = resultado.get("codigo")
    if resultado.get("ok"):
        log("INFO", "Webhook aceptado", id_evento=id_evento,
            archivo=metadatos["nombre"], respuesta=resultado.get("mensaje"),
            tiempo_respuesta_ms=ms)
    elif codigo == 500:
        # Error del servidor: puede ser transitorio, se reintenta
        log("ERROR", "El webhook respondió con error interno",
            id_evento=id_evento, respuesta=resultado, tiempo_respuesta_ms=ms)
        raise RuntimeError("Error interno del webhook")
    else:
        # 400, 401, 408: rechazo definitivo, no se reintenta
        log("ERROR", "El webhook rechazó el mensaje", id_evento=id_evento,
            respuesta=resultado, tiempo_respuesta_ms=ms)