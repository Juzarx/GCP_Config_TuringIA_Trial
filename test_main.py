"""
Pruebas unitarias para la Cloud Function procesar_archivo.

Decisiones técnicas:
- Se prueba extraer_metadatos de forma aislada, porque contiene la lógica
  real y no depende de GCP.
- Para procesar_archivo se simula un CloudEvent con un objeto sencillo,
  sin necesidad de desplegar nada.
"""
import json
from types import SimpleNamespace

import pytest

from main import extraer_metadatos, procesar_archivo


def crear_evento(data, id_evento="evento-123"):
    """Simula un CloudEvent: soporta cloud_event['id'] y cloud_event.data."""

    class EventoFalso(dict):
        pass

    evento = EventoFalso(id=id_evento)
    evento.data = data
    return evento


# ---------- Flujo exitoso ----------

def test_extraer_metadatos_correctos():
    data = {
        "bucket": "mi-bucket",
        "name": "foto.png",
        "size": "2048",
        "contentType": "image/png",
    }
    resultado = extraer_metadatos(data)

    assert resultado["nombre"] == "foto.png"
    assert resultado["bucket"] == "mi-bucket"
    assert resultado["tamano_bytes"] == 2048  # se convierte a entero
    assert resultado["tipo_contenido"] == "image/png"


def test_extraer_metadatos_usa_valores_por_defecto():
    data = {"bucket": "mi-bucket", "name": "archivo.bin"}
    resultado = extraer_metadatos(data)

    assert resultado["tamano_bytes"] == 0
    assert resultado["tipo_contenido"] == "desconocido"


def test_procesar_archivo_registra_log_exitoso(capsys):
    evento = crear_evento(
        {"bucket": "mi-bucket", "name": "doc.pdf", "size": "100",
         "contentType": "application/pdf"}
    )
    procesar_archivo(evento)

    salida = capsys.readouterr().out
    assert "Archivo procesado correctamente" in salida
    assert "doc.pdf" in salida


# ---------- Manejo de errores ----------

def test_extraer_metadatos_sin_nombre_lanza_error():
    with pytest.raises(ValueError):
        extraer_metadatos({"bucket": "mi-bucket"})


def test_extraer_metadatos_sin_bucket_lanza_error():
    with pytest.raises(ValueError):
        extraer_metadatos({"name": "archivo.txt"})


def test_extraer_metadatos_datos_no_son_diccionario():
    with pytest.raises(ValueError):
        extraer_metadatos("esto no es un diccionario")


def test_procesar_archivo_evento_invalido_registra_error(capsys):
    evento = crear_evento({"bucket": "mi-bucket"})  # falta 'name'
    procesar_archivo(evento)  # no debe lanzar excepción

    registro = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert registro["severity"] == "ERROR"
    assert "datos inválidos" in registro["message"]