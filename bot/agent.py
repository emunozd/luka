"""
bot/agent.py — Agente conversacional LUKA
==========================================
Flujo: clasificador liviano → acción directa o modelo completo.
El clasificador es una llamada rápida al modelo que devuelve
una sola palabra: GASTO, REPORTE, ULTIMOS, BORRAR, u OTRO.
Los comandos /gasto, /reporte etc. siguen funcionando exactamente igual.
"""
import logging
import os
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

_MLX_BASE  = os.environ.get("MLX_SERVER_URL", "http://127.0.0.1:8181/luka")
AIBASE_URL = _MLX_BASE.rsplit("/luka", 1)[0]
API_URL    = os.environ.get("LUKA_API_URL", "http://luka-api:8000")

MES_ACTUAL = datetime.now().strftime("%Y-%m")

# ─────────────────────────────────────────────────────────────────────────────
# Clasificador de intención
# ─────────────────────────────────────────────────────────────────────────────

PROMPT_CLASIFICADOR = """Clasifica el mensaje del usuario en UNA sola palabra.

Responde ÚNICAMENTE con una de estas palabras, sin puntuación ni explicación:
- GASTO     → si el usuario menciona que pagó, compró, gastó, le costó, adquirió algo
- REPORTE   → si el usuario pide resumen, reporte o cuánto ha gastado (en cualquier período)
- ULTIMOS   → si el usuario quiere ver sus últimos gastos, historial o movimientos recientes
- BORRAR    → si el usuario quiere eliminar o borrar un gasto
- OTRO      → cualquier otra cosa

Mensaje: {texto}"""


def _clasificar_intencion(texto: str) -> str:
    """Llama al modelo con prompt mínimo y devuelve la intención en una palabra."""
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{AIBASE_URL}/v1/chat/completions",
            json={
                "model":    "luka",
                "messages": [{"role": "user", "content": PROMPT_CLASIFICADOR.format(texto=texto)}],
                "max_tokens": 10,
            },
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        respuesta = r.json()

    raw      = respuesta["choices"][0]["message"].get("content", "OTRO").strip().upper()
    primera  = raw.split()[0] if raw.split() else "OTRO"
    if primera in ("GASTO", "REPORTE", "ULTIMOS", "BORRAR"):
        return primera
    return "OTRO"


# ─────────────────────────────────────────────────────────────────────────────
# System prompt para respuestas finales del modelo
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_RESPUESTA = f"""Eres LUKA, el asistente de finanzas personales para colombianos.
Responde SIEMPRE en español colombiano. Sé conciso y amigable.
FECHA ACTUAL: {MES_ACTUAL}
NUNCA respondas preguntas fuera de finanzas personales del usuario.
Si el contexto no aplica a finanzas personales, responde: FUERA_DE_SCOPE

REGLAS DE FORMATO:
- Presenta los datos claramente y agrega UN consejo corto y directo al final si es relevante.
- NUNCA termines con una pregunta. NUNCA ofrezcas hacer más cosas.
- El consejo debe ser una observación útil y corta, no una oferta de servicio.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers HTTP
# ─────────────────────────────────────────────────────────────────────────────

def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ─────────────────────────────────────────────────────────────────────────────
# Acciones directas
# ─────────────────────────────────────────────────────────────────────────────

def _accion_gasto(texto: str, token: str) -> dict:
    """Categoriza el gasto via AIBase y retorna preview para confirmación."""
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"{AIBASE_URL}/luka/categorizar-gasto-manual",
            json={"descripcion": texto},
        )
        r.raise_for_status()
        items = r.json()

    if not items:
        return {"tipo": "texto", "respuesta": "❌ No pude entender el gasto. Intenta ser más específico."}

    categorias    = {}
    descripciones = {}
    for item in items:
        cat   = item["categoria"]
        monto = item.get("monto")
        if monto is None:
            return {"tipo": "texto", "respuesta": "❌ No detecté el monto. Incluye el valor, por ejemplo: 'gasté 15mil en el bus'."}
        desc               = item.get("descripcion") or texto
        categorias[cat]    = float(monto)
        descripciones[cat] = desc

    total          = sum(categorias.values())
    lineas_preview = ["📋 Esto es lo que voy a registrar:\n"]
    for cat, monto in categorias.items():
        lineas_preview.append(f"- <b>{cat}</b>: ${float(monto):,.0f} — {descripciones[cat]}")
    lineas_preview.append(f"\n💰 Total: ${total:,.0f}")

    return {
        "tipo":      "confirmar_gasto",
        "respuesta": "\n".join(lineas_preview),
        "preview": {
            "raw_text":      texto,
            "categorias":    categorias,
            "descripciones": descripciones,
        },
    }


def _obtener_ultimos(token: str) -> list:
    """Devuelve lista cruda de últimos 5 registros combinados."""
    with httpx.Client(timeout=30.0) as client:
        r_gastos = client.get(f"{API_URL}/gastos/manual", headers=_headers(token))
        r_gastos.raise_for_status()
        gastos = r_gastos.json()

        r_facturas = client.get(f"{API_URL}/facturas/", headers=_headers(token))
        r_facturas.raise_for_status()
        facturas = r_facturas.json()

    combinados = []
    for g in gastos:
        combinados.append({
            "id":          g["id"],
            "tipo":        "gasto_manual",
            "descripcion": g["descripcion"],
            "monto":       g["monto"],
            "fecha":       g["fecha"],
        })
    for f in facturas:
        combinados.append({
            "id":          f["id"],
            "tipo":        "factura",
            "descripcion": f.get("comercio") or "Factura sin comercio",
            "monto":       f.get("total") or 0,
            "fecha":       f.get("fecha_factura") or f.get("creado_en", "")[:10],
        })

    combinados.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    return combinados[:5]


def _accion_ultimos_contexto(ultimos: list) -> str:
    """Versión con IDs para pasarle al modelo en el flujo de BORRAR."""
    if not ultimos:
        return "No tienes gastos registrados."
    lines = ["Últimos registros:"]
    for i, r in enumerate(ultimos, 1):
        tipo_label = "factura" if r["tipo"] == "factura" else "gasto manual"
        lines.append(
            f"{i}. [{r['id']}] [{tipo_label}] {r['descripcion']} — "
            f"${float(r['monto']):,.0f} ({r['fecha']})"
        )
    return "\n".join(lines)


def _accion_reporte(texto: str, token: str) -> str:
    """Determina el mes y obtiene datos del reporte."""
    match = re.search(r"(20\d{2})[.\-/](0[1-9]|1[0-2])", texto)
    if match:
        mes = f"{match.group(1)}-{match.group(2)}"
    elif any(p in texto.lower() for p in ("mes pasado", "mes anterior", "último mes", "ultimo mes")):
        year  = int(MES_ACTUAL[:4])
        month = int(MES_ACTUAL[5:])
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        mes = f"{year}-{month:02d}"
    else:
        mes = MES_ACTUAL

    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{API_URL}/reportes/mensual",
            params={"mes": mes},
            headers=_headers(token),
        )
        r.raise_for_status()
        data = r.json()

    if not data:
        return f"No hay gastos registrados para {mes}."

    total = sum(float(item["total"]) for item in data)
    lines = [f"Reporte {mes}:"]
    for item in data:
        lines.append(f"- {item['categoria']}: ${float(item['total']):,.0f}")
    lines.append(f"Total: ${total:,.0f}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Respuesta final via modelo
# ─────────────────────────────────────────────────────────────────────────────

def _respuesta_modelo(texto_usuario: str, contexto: str) -> str:
    """Llama al modelo con el contexto ya resuelto para que genere respuesta natural."""
    prompt = f"Pregunta del usuario: {texto_usuario}\n\nDatos disponibles:\n{contexto}"
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{AIBASE_URL}/v1/chat/completions",
            json={
                "model":    "luka",
                "messages": [
                    {"role": "system", "content": SYSTEM_RESPUESTA},
                    {"role": "user",   "content": prompt},
                ],
                "max_tokens": 400,
            },
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        respuesta = r.json()

    texto = respuesta["choices"][0]["message"].get("content", "").strip()
    if texto == "FUERA_DE_SCOPE":
        return "Solo puedo ayudarte con tus finanzas personales: registrar gastos, ver reportes o revisar tu historial. 💸"
    return texto


# ─────────────────────────────────────────────────────────────────────────────
# Punto de entrada principal
# ─────────────────────────────────────────────────────────────────────────────

def agente_luka(texto: str, token: str, ultimos_guardados: list = None) -> dict:
    """
    Retorna:
      {"tipo": "texto",             "respuesta": str}
      {"tipo": "confirmar_gasto",   "respuesta": str, "preview": dict}
      {"tipo": "confirmar_borrado", "respuesta": str, "id": str, "descripcion": str, "monto": float}
      {"tipo": "ultimos",           "respuesta": str, "registros": list}

    ultimos_guardados: lista de registros ya consultados previamente (para BORRAR sin re-consultar).
    """
    logger.info("Clasificando intención: %r", texto)
    intencion = _clasificar_intencion(texto)
    logger.info("Intención detectada: %s", intencion)

    # ── GASTO → acción directa, sin modelo ───────────────────────────────────
    if intencion == "GASTO":
        return _accion_gasto(texto, token)

    # ── REPORTE → obtener datos, modelo formatea ──────────────────────────────
    if intencion == "REPORTE":
        contexto  = _accion_reporte(texto, token)
        respuesta = _respuesta_modelo(texto, contexto)
        return {"tipo": "texto", "respuesta": respuesta}

    # ── ULTIMOS → formato fijo en Python, sin modelo ──────────────────────────
    if intencion == "ULTIMOS":
        ultimos = _obtener_ultimos(token)

        if not ultimos:
            return {"tipo": "texto", "respuesta": "📭 No tienes gastos registrados aún."}

        lineas = ["📋 <b>Últimos registros:</b>\n"]
        for i, r in enumerate(ultimos, 1):
            icono = "🧾" if r["tipo"] == "factura" else "💸"
            lineas.append(
                f"{i}. {icono} <b>{r['descripcion']}</b> — ${float(r['monto']):,.0f}"
                f"\n    📅 {r['fecha']}"
            )
        lineas.append("\n¿Quieres borrar alguno? Dime el número.")

        return {
            "tipo":      "ultimos",
            "respuesta": "\n".join(lineas),
            "registros": ultimos,
        }

    # ── BORRAR → usar lista guardada o consultar fresca ───────────────────────
    if intencion == "BORRAR":
        # Usar la lista que el usuario ya vio si está disponible
        ultimos = ultimos_guardados if ultimos_guardados else _obtener_ultimos(token)

        if not ultimos:
            return {"tipo": "texto", "respuesta": "📭 No tienes gastos registrados para borrar."}

        contexto_borrar = _accion_ultimos_contexto(ultimos)
        prompt_borrar = (
            f"El usuario quiere borrar un gasto. Sus últimos registros son:\n{contexto_borrar}\n\n"
            f"Identifica cuál quiere borrar basándote en: '{texto}'\n"
            f"Si puedes identificarlo con certeza, responde ÚNICAMENTE con este formato exacto en una sola línea, sin texto adicional:\n"
            f"BORRAR_PENDIENTE|<id>|<descripcion>|<monto>\n"
            f"El monto debe ser número puro sin símbolo ni comas. Sin saltos de línea. Sin explicaciones.\n"
            f"Si no puedes identificarlo con certeza, responde en español preguntando cuál es."
        )
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{AIBASE_URL}/v1/chat/completions",
                json={
                    "model":    "luka",
                    "messages": [
                        {"role": "system", "content": SYSTEM_RESPUESTA},
                        {"role": "user",   "content": prompt_borrar},
                    ],
                    "max_tokens": 100,
                },
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            resp_borrar = r.json()

        texto_borrar = resp_borrar["choices"][0]["message"].get("content", "").strip()
        if texto_borrar.startswith("BORRAR_PENDIENTE|"):
            partes    = texto_borrar.split("|")
            #monto_raw = partes[3].replace("$", "").replace(",", "").strip()
            monto_raw = partes[3].split("\n")[0].replace("$", "").replace(",", "").strip()
            return {
                "tipo":        "confirmar_borrado",
                "id":          partes[1],
                "descripcion": partes[2],
                "monto":       float(monto_raw),
                "respuesta":   f"¿Eliminar <b>{partes[2]}</b> — ${float(monto_raw):,.0f}?",
            }
        return {"tipo": "texto", "respuesta": texto_borrar}

    # ── OTRO → respuesta directa del modelo ──────────────────────────────────
    respuesta = _respuesta_modelo(
        texto,
        "El usuario hizo una pregunta fuera del scope de finanzas personales del usuario."
    )
    return {"tipo": "texto", "respuesta": respuesta}