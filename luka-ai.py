"""
luka-ai.py — Microservicio de inferencia IA para LUKA
======================================================
Corre nativo en la Mac (Apple Silicon).
Expone endpoints internos consumidos por luka-api (Docker).
Solo escucha en 127.0.0.1 — invisible fuera de la Mac.

Arranque:
    source ~/mlx-env/bin/activate
    python luka-ai.py
"""

import json
import logging
import os
import re
import tempfile
import uuid
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config
from PIL import Image
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
MODEL_PATH = "mlx-community/Qwen3.5-35B-A3B-4bit"
HOST       = "127.0.0.1"
PORT       = 8181
IMG_MAX_PX = 1024

CATEGORIAS_VALIDAS = {
    "HOGAR", "CANASTA", "MEDICAMENTOS", "OCIO", "ANTOJO",
    "TRANSPORTE", "TECNOLOGÍA", "ROPA", "EDUCACIÓN", "MASCOTAS",
}

# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "Eres LUKA, el motor de análisis financiero de una app de finanzas personales para colombianos. "
    "Tu única función es clasificar gastos en categorías y devolver JSON válido. "
    "Responde SIEMPRE en español. NUNCA uses otro idioma. "
    "NUNCA expliques tu razonamiento. SOLO devuelve el JSON solicitado, sin texto adicional."
)

PROMPT_FACTURA = """Analiza el siguiente contenido de una factura o recibo y clasifica el gasto total por categorías.

CATEGORÍAS DISPONIBLES (usa exactamente estos nombres):
HOGAR, CANASTA, MEDICAMENTOS, OCIO, ANTOJO, TRANSPORTE, TECNOLOGÍA, ROPA, EDUCACIÓN, MASCOTAS

REGLAS:
- CANASTA: mercado del día a día, alimentos básicos, aseo del hogar.
- ANTOJO: comida por placer, restaurantes, domicilios, dulces, snacks.
- HOGAR: arriendo, servicios públicos, reparaciones, muebles.
- MEDICAMENTOS: farmacia, consultas médicas, parafarmacia.
- OCIO: streaming, entretenimiento, deportes, viajes.
- TRANSPORTE: gasolina, taxi, bus, peajes, Uber.
- TECNOLOGÍA: celulares, computadores, software, internet.
- ROPA: ropa, calzado, accesorios de vestir.
- EDUCACIÓN: cursos, libros, útiles escolares, matrículas.
- MASCOTAS: veterinario, concentrado, accesorios para mascotas.
- Solo incluye categorías con valor > 0.
- Los totales deben ser números con máximo 2 decimales.

{contenido}

Devuelve ÚNICAMENTE este JSON, sin explicaciones ni texto adicional:
{{
  "categorias": {{"CATEGORIA": total_en_pesos}},
  "comercio": "nombre del comercio o null",
  "fecha": "YYYY-MM-DD o null",
  "total_factura": total_general_en_pesos
}}"""

PROMPT_GASTO_MANUAL = """El usuario describió uno o varios gastos con sus propias palabras. Extrae TODOS los gastos mencionados.

CATEGORÍAS DISPONIBLES (usa exactamente estos nombres):
HOGAR, CANASTA, MEDICAMENTOS, OCIO, ANTOJO, TRANSPORTE, TECNOLOGÍA, ROPA, EDUCACIÓN, MASCOTAS

REGLAS PARA EL MONTO:
- Interpreta cualquier formato colombiano: 5k → 5000, 5mil → 5000, 5.000 → 5000, 5,000 → 5000, 5 lucas → 5000.
- El monto siempre es en pesos colombianos (COP).
- Si un gasto no tiene monto claro, usa null.
- Si hay varios gastos, devuelve uno por ítem.

REGLAS PARA LA CATEGORÍA:
- CANASTA: pan, arroz, leche, huevos, frutas, verduras, mercado básico.
- ANTOJO: gaseosa, snacks, dulces, comida por placer, restaurante, domicilio.
- MASCOTAS: comida para perro/gato, veterinario, accesorios de mascotas.
- HOGAR: servicios públicos, arriendo, reparaciones.
- MEDICAMENTOS: drogas, farmacia, consulta médica.
- OCIO: entretenimiento, streaming, deporte.
- TRANSPORTE: bus, taxi, Uber, gasolina, peaje.
- TECNOLOGÍA: celular, computador, internet, software.
- ROPA: ropa, zapatos, accesorios de vestir.
- EDUCACIÓN: libros, cursos, útiles, matrícula.

DESCRIPCIÓN: {descripcion}

Devuelve ÚNICAMENTE este JSON, sin explicaciones ni texto adicional:
[
  {{"categoria": "NOMBRE_CATEGORIA", "monto": valor_numerico_o_null, "descripcion": "descripcion corta del item"}},
  ...
]"""

# ─────────────────────────────────────────────────────────────────────────────
# Singleton del modelo
# ─────────────────────────────────────────────────────────────────────────────
class _Modelo:
    _model: Any = None
    _processor: Any = None
    _config: Any = None

    @classmethod
    def cargar(cls):
        if cls._model is None:
            logger.info("Cargando modelo: %s", MODEL_PATH)
            cls._model, cls._processor = load(MODEL_PATH)
            cls._config = load_config(MODEL_PATH)
            logger.info("Modelo listo.")

    @classmethod
    def get(cls):
        return cls._model, cls._processor, cls._config


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    _Modelo.cargar()
    yield

app = FastAPI(title="LUKA AI", lifespan=lifespan)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _inferir_texto(prompt_usuario: str, max_tokens: int = 512) -> str:
    model, processor, config = _Modelo.get()

    prompt = apply_chat_template(
        processor,
        config,
        prompt_usuario,
        num_images=0,
        enable_thinking=False,
    )

    result = generate(
        model,
        processor,
        prompt,
        max_tokens=max_tokens,
        verbose=False,
    )
    return result.text.strip() if hasattr(result, "text") else str(result).strip()


def _inferir_imagen(prompt_usuario: str, imagen_b64: str, max_tokens: int = 600) -> str:
    model, processor, config = _Modelo.get()

    # Decodificar, redimensionar y guardar en disco temporal
    img_bytes = __import__("base64").b64decode(imagen_b64)
    tmp_path = os.path.join(tempfile.gettempdir(), f"luka_img_{uuid.uuid4().hex}.jpg")
    try:
        img = Image.open(__import__("io").BytesIO(img_bytes))
        img.thumbnail((IMG_MAX_PX, IMG_MAX_PX))
        img.save(tmp_path, "JPEG")

        prompt = apply_chat_template(
            processor,
            config,
            prompt_usuario,
            num_images=1,
            enable_thinking=False,
        )

        result = generate(
            model,
            processor,
            prompt,
            image=tmp_path,
            max_tokens=max_tokens,
            verbose=False,
        )
        return result.text.strip() if hasattr(result, "text") else str(result).strip()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _extraer_json(texto: str) -> Any:
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", texto, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No se pudo extraer JSON válido: {texto!r}")


def _validar_categorias(categorias: dict) -> dict:
    resultado = {}
    for cat, total in categorias.items():
        cat_upper = cat.upper().strip()
        if cat_upper in CATEGORIAS_VALIDAS:
            try:
                valor = float(total)
                if valor > 0:
                    resultado[cat_upper] = round(valor, 2)
            except (TypeError, ValueError):
                logger.warning("Valor inválido para categoría %s: %s", cat, total)
        else:
            logger.warning("Categoría desconocida ignorada: %s", cat)
    if not resultado:
        raise ValueError("El modelo no devolvió ninguna categoría válida.")
    return resultado


def _validar_gastos_manuales(data: Any) -> list:
    if not isinstance(data, list):
        data = [data]
    resultado = []
    for item in data:
        categoria = str(item.get("categoria", "")).upper().strip()
        if categoria not in CATEGORIAS_VALIDAS:
            logger.warning("Categoría inválida ignorada: %s", categoria)
            continue
        monto_raw = item.get("monto")
        monto = round(float(monto_raw), 2) if monto_raw is not None else None
        descripcion = item.get("descripcion", "").strip() or None
        resultado.append({
            "categoria":   categoria,
            "monto":       monto,
            "descripcion": descripcion,
        })
    if not resultado:
        raise ValueError("El modelo no devolvió ningún gasto válido.")
    return resultado


def _procesar_resultado_factura(data: dict) -> dict:
    categorias = _validar_categorias(data.get("categorias", {}))
    return {
        "categorias":    categorias,
        "comercio":      data.get("comercio") or None,
        "fecha":         data.get("fecha") or None,
        "total_factura": round(float(data.get("total_factura") or sum(categorias.values())), 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────
class TextoRequest(BaseModel):
    texto: str

class ImagenRequest(BaseModel):
    imagen_b64: str

class GastoManualRequest(BaseModel):
    descripcion: str


@app.post("/categorizar-factura-texto")
def categorizar_factura_texto(req: TextoRequest):
    if not req.texto.strip():
        raise HTTPException(status_code=422, detail="El texto no puede estar vacío.")
    try:
        prompt = PROMPT_FACTURA.format(
            contenido=f"TEXTO DE LA FACTURA:\n{req.texto.strip()}"
        )
        data = _extraer_json(_inferir_texto(prompt, max_tokens=600))
        return _procesar_resultado_factura(data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/categorizar-factura-imagen")
def categorizar_factura_imagen(req: ImagenRequest):
    if not req.imagen_b64.strip():
        raise HTTPException(status_code=422, detail="La imagen no puede estar vacía.")
    try:
        prompt = PROMPT_FACTURA.format(
            contenido="Analiza la imagen del recibo o factura que se adjunta."
        )
        data = _extraer_json(_inferir_imagen(prompt, req.imagen_b64, max_tokens=600))
        return _procesar_resultado_factura(data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/categorizar-gasto-manual")
def categorizar_gasto_manual(req: GastoManualRequest):
    if not req.descripcion.strip():
        raise HTTPException(status_code=422, detail="La descripción no puede estar vacía.")
    try:
        data = _extraer_json(
            _inferir_texto(
                PROMPT_GASTO_MANUAL.format(descripcion=req.descripcion.strip()),
                max_tokens=300,
            )
        )
        return _validar_gastos_manuales(data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok", "service": "luka-ai"}


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
