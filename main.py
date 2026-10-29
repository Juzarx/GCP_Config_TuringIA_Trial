"""
Cloud Function que se activa al subir un archivo a un bucket de Cloud Storage.

Decisiones técnicas:
- Se usa un trigger de tipo `finalized`: solo se dispara cuando el objeto
  terminó de subirse por completo.
- Los logs se escriben en JSON a stdout. Cloud Logging interpreta los campos
  `severity` y `message`, lo que permite filtrar por nivel de severidad y
  por campos propios (bucket, archivo, etc.) sin instalar librerías extra.
- La extracción de metadatos está separada del manejador del evento para
  poder probarla de forma unitaria.
"""
import json

import functions_framework


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


@functions_framework.cloud_event
def procesar_archivo(cloud_event):
    """Punto de entrada: se ejecuta por cada archivo subido al bucket."""
    log("INFO", "Evento recibido", id_evento=cloud_event["id"])

    try:
        metadatos = extraer_metadatos(cloud_event.data)
        log("INFO", "Archivo procesado correctamente", **metadatos)
        return metadatos

    except (ValueError, TypeError) as error:
        # Error de datos: el evento es inválido, reintentar no ayudaría
        log("ERROR", f"Evento con datos inválidos: {error}")

    except Exception as error:
        # Error inesperado: se registra con detalle para depurar
        log("CRITICAL", f"Error inesperado: {error}", tipo_error=type(error).__name__)
        raise  # se relanza para que quede marcado como fallo en GCP