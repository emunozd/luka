"""
ai_client.py — Cliente HTTP hacia el microservicio luka-ai
==========================================================
Toda la inferencia ocurre en luka-ai.py (Mac nativa).
Este módulo corre dentro del contenedor Docker.
"""
import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


def _post(endpoint: str, payload: dict, timeout: float = 120.0) -> any:
    url = f"{settings.mlx_server_url}/{endpoint}"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError:
        raise RuntimeError(
            f"No se pudo conectar al servicio de IA en {settings.mlx_server_url}. "
            "Verifica que luka-ai.py esté corriendo."
        )
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        raise ValueError(detail)


def categorizar_factura_texto(texto: str) -> dict:
    return _post("categorizar-factura-texto", {"texto": texto})


def categorizar_factura_imagen(imagen_b64: str) -> dict:
    # Dos pasadas en AIBase (transcripción + clasificación) → timeout extendido
    return _post("categorizar-factura-imagen", {"imagen_b64": imagen_b64}, timeout=300.0)


def categorizar_gasto_manual(descripcion: str) -> list:
    return _post("categorizar-gasto-manual", {"descripcion": descripcion})