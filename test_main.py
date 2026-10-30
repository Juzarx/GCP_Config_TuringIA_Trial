"""
Pruebas unitarias de la Cloud Function.

Decisiones técnicas:
- enviar_webhook se reemplaza con monkeypatch para no hacer llamadas de red.
- Se prueban tanto el flujo exitoso como cada rama de error: evento inválido,
  falta de configuración, rechazo definitivo (no se reintenta), error 500 y
  fallo de red (ambos se relanzan para que Eventarc reintente).
"""
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
import requests

import main
from main import construir_payload, extraer_metadatos, firmar, procesar_archivo


def crear_evento(data, id_evento="evento-123"):
    """Simula un CloudEvent: soporta cloud_event['id'] y cloud_event.data."""

    class EventoFalso(dict):
        pass

    evento = EventoFalso(id=id_evento)
    evento.data = data
    return evento


DATOS_VALIDOS = {
    "bucket": "mi-bucket", "name": "doc.pdf", "size": "100",
    "contentType": "application/pdf",
}


@pytest.fixture
def entorno(monkeypatch):
    """Variables de entorno que normalmente vienen de Cloud Run y Secret Manager."""
    monkeypatch.setenv("WEBHOOK_URL", "https://ejemplo.test/exec")
    monkeypatch.setenv("WEBHOOK_SECRET", "secreto-de-prueba")


def logs(capsys):
    """Convierte la salida impresa en una lista de diccionarios."""
    lineas = capsys.readouterr().out.strip().splitlines()
    return [json.loads(l) for l in lineas if l.startswith("{")]


# ---------- extraer_metadatos ----------

def test_extraer_metadatos_correctos():
    resultado = extraer_metadatos(DATOS_VALIDOS)
    assert resultado["nombre"] == "doc.pdf"
    assert resultado["tamano_bytes"] == 100  # se convierte a entero
    assert resultado["tipo_contenido"] == "application/pdf"


def test_extraer_metadatos_valores_por_defecto():
    resultado = extraer_metadatos({"bucket": "b", "name": "a.bin"})
    assert resultado["tamano_bytes"] == 0
    assert resultado["tipo_contenido"] == "desconocido"


@pytest.mark.parametrize("data", [
    {"bucket": "mi-bucket"},          # falta name
    {"name": "archivo.txt"},          # falta bucket
    "esto no es un diccionario",
])
def test_extraer_metadatos_invalidos_lanzan_error(data):
    with pytest.raises(ValueError):
        extraer_metadatos(data)


# ---------- firma y payload ----------

def test_firmar_coincide_con_hmac_sha256():
    esperado = hmac.new(b"clave", b"mensaje", hashlib.sha256).hexdigest()
    assert firmar("mensaje", "clave") == esperado


def test_firma_cambia_si_cambia_el_payload():
    assert firmar("a", "clave") != firmar("b", "clave")


def test_construir_payload_incluye_id_y_timestamp():
    payload = json.loads(construir_payload({"nombre": "a.txt"}, "evt-1"))
    assert payload["id_evento"] == "evt-1"
    assert isinstance(payload["timestamp"], int)


# ---------- procesar_archivo: flujo exitoso ----------

def test_procesar_archivo_exitoso(monkeypatch, capsys, entorno):
    monkeypatch.setattr(main, "enviar_webhook", lambda p, s, u: (
        {"ok": True, "codigo": 200, "mensaje": "Registrado y notificado"}, 150))

    procesar_archivo(crear_evento(DATOS_VALIDOS))

    mensajes = [l["message"] for l in logs(capsys)]
    assert "Webhook aceptado" in mensajes


# ---------- procesar_archivo: manejo de errores ----------

def test_evento_invalido_no_llama_al_webhook(monkeypatch, capsys, entorno):
    def no_debe_llamarse(*args):
        raise AssertionError("No debería llamar al webhook")
    monkeypatch.setattr(main, "enviar_webhook", no_debe_llamarse)

    procesar_archivo(crear_evento({"bucket": "mi-bucket"}))  # falta name

    registro = logs(capsys)[-1]
    assert registro["severity"] == "ERROR"
    assert "datos inválidos" in registro["message"]


def test_sin_configuracion_lanza_error(monkeypatch, capsys):
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)

    with pytest.raises(RuntimeError):
        procesar_archivo(crear_evento(DATOS_VALIDOS))
    assert logs(capsys)[-1]["severity"] == "CRITICAL"


def test_rechazo_401_se_registra_y_no_se_reintenta(monkeypatch, capsys, entorno):
    monkeypatch.setattr(main, "enviar_webhook", lambda p, s, u: (
        {"ok": False, "codigo": 401, "mensaje": "Firma inválida"}, 90))

    procesar_archivo(crear_evento(DATOS_VALIDOS))  # no debe lanzar excepción

    assert logs(capsys)[-1]["severity"] == "ERROR"


def test_error_500_del_webhook_se_relanza(monkeypatch, entorno):
    monkeypatch.setattr(main, "enviar_webhook", lambda p, s, u: (
        {"ok": False, "codigo": 500, "mensaje": "Error interno"}, 90))

    with pytest.raises(RuntimeError):
        procesar_archivo(crear_evento(DATOS_VALIDOS))


def test_error_de_red_se_relanza(monkeypatch, entorno):
    def falla_red(*args):
        raise requests.ConnectionError("sin conexión")
    monkeypatch.setattr(main, "enviar_webhook", falla_red)

    with pytest.raises(requests.ConnectionError):
        procesar_archivo(crear_evento(DATOS_VALIDOS))