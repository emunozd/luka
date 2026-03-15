"""
bot/agent.py — Agente conversacional LUKA con tool calling
===========================================================
Recibe texto libre del usuario y decide qué acción ejecutar
consultando AIBase con tools definidas. Los comandos /gasto,
/reporte etc. siguen funcionando exactamente igual.
"""
import json
import logging
import os
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

_MLX_BASE  = os.environ.get("MLX_SERVER_URL", "http://127.0.0.1:8181/luka")
AIBASE_URL = _MLX_BASE.rsplit("/luka", 1)[0]
API_URL    = os.environ.get("LUKA_API_URL", "http://luka-api:8000")

MES_ACTUAL = datetime.now().strftime("%Y-%m")

SYSTEM_PROMPT = f"""Eres LUKA, el asistente de finanzas personales para colombianos.
El usuario te habla por Telegram en lenguaje natural. Tu trabajo es entender qué quiere y ejecutar la acción correcta.

FECHA ACTUAL: {MES_ACTUAL}

REGLAS:
- Si el usuario menciona un gasto (pagué, compré, gasté, me costó, etc.) → usa registrar_gasto.
- Si el usuario quiere saber cuánto ha gastado, un resumen o reporte → usa ver_reporte.
- Si el usuario quiere ver sus últimos gastos → usa ver_ultimos.
- Si el usuario quiere borrar o eliminar un gasto y ya tienes el ID → usa borrar_gasto.
- Si el usuario quiere borrar pero no sabes cuál → llama ver_ultimos primero, luego pregunta cuál.
- Si no es ninguna de las anteriores → responde directamente en texto, amigable y breve.
- NUNCA respondas en inglés. SIEMPRE en español colombiano.
- Sé conciso. Máximo 3 líneas cuando no hay datos que mostrar.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "registrar_gasto",
            "description": (
                "Registra uno o varios gastos del usuario. Úsala cuando el usuario mencione "
                "que pagó, compró, gastó o invirtió en algo. Pasa exactamente lo que el usuario escribió."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "descripcion": {
                        "type": "string",
                        "description": "Texto del usuario describiendo el gasto. Ej: 'gasté 15mil en el bus y 8mil en tinto'",
                    }
                },
                "required": ["descripcion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ver_reporte",
            "description": (
                "Muestra el resumen de gastos por categoría. "
                "Úsala cuando el usuario pregunte cuánto ha gastado, pida reporte o resumen del mes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mes": {
                        "type": "string",
                        "description": f"Mes en formato YYYY-MM. Si no se especifica, usa el mes actual: {MES_ACTUAL}",
                    }
                },
                "required": ["mes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ver_ultimos",
            "description": (
                "Lista los últimos 5 gastos registrados. "
                "Úsala cuando el usuario quiera ver qué ha gastado recientemente o antes de borrar."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "borrar_gasto",
            "description": "Elimina un gasto específico por su ID UUID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "gasto_id": {
                        "type": "string",
                        "description": "ID UUID del gasto a eliminar.",
                    }
                },
                "required": ["gasto_id"],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Ejecución de tools → llaman a luka-api
# ─────────────────────────────────────────────────────────────────────────────

def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _ejecutar_tool(nombre: str, args: dict, token: str) -> str:
    try:
        if nombre == "registrar_gasto":
            return _tool_registrar_gasto(args["descripcion"], token)
        elif nombre == "ver_reporte":
            return _tool_ver_reporte(args.get("mes", MES_ACTUAL), token)
        elif nombre == "ver_ultimos":
            return _tool_ver_ultimos(token)
        elif nombre == "borrar_gasto":
            return _tool_borrar_gasto(args["gasto_id"], token)
        else:
            return f"Tool desconocida: {nombre}"
    except httpx.HTTPStatusError as e:
        logger.error("HTTP error en tool %s: %s", nombre, e)
        return f"Error al ejecutar {nombre}: {e.response.status_code}"
    except Exception as e:
        logger.error("Error en tool %s: %s", nombre, e)
        return f"Error al ejecutar {nombre}: {str(e)}"


def _tool_registrar_gasto(descripcion: str, token: str) -> str:
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"{API_URL}/gastos/manual",
            json={"canal": "telegram", "descripcion": descripcion},
            headers=_headers(token),
        )
        r.raise_for_status()
        gastos = r.json()
    if not gastos:
        return "No se pudo registrar ningún gasto."
    lines = ["Gastos registrados:"]
    for g in gastos:
        lines.append(f"- {g['categoria']}: ${float(g['monto']):,.0f} — {g['descripcion']}")
    return "\n".join(lines)


def _tool_ver_reporte(mes: str, token: str) -> str:
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


def _tool_ver_ultimos(token: str) -> str:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(f"{API_URL}/gastos/manual", headers=_headers(token))
        r.raise_for_status()
        gastos = r.json()[:5]
    if not gastos:
        return "No tienes gastos registrados."
    lines = ["Últimos gastos:"]
    for i, g in enumerate(gastos, 1):
        lines.append(
            f"{i}. [{g['id']}] {g['categoria']} — ${float(g['monto']):,.0f}"
            f" ({g['descripcion']}) {g['fecha']}"
        )
    return "\n".join(lines)


def _tool_borrar_gasto(gasto_id: str, token: str) -> str:
    with httpx.Client(timeout=30.0) as client:
        r = client.delete(
            f"{API_URL}/gastos/manual/{gasto_id}",
            headers=_headers(token),
        )
        r.raise_for_status()
    return "Gasto eliminado correctamente."


# ─────────────────────────────────────────────────────────────────────────────
# Loop del agente
# ─────────────────────────────────────────────────────────────────────────────

def agente_luka(texto: str, token: str) -> str:
    messages = [{"role": "user", "content": texto}]

    with httpx.Client(timeout=120.0) as client:
        # Ronda 1 — usar /v1/messages (Anthropic-compatible, tool calling funcional)
        r = client.post(
            f"{AIBASE_URL}/v1/messages",
            json={
                "model":       "luka",
                "messages":    messages,
                "tools":       TOOLS,
                "tool_choice": {"type": "auto"},
                "max_tokens":  512,
                "system":      SYSTEM_PROMPT,
            },
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        respuesta = r.json()

    # Parsear respuesta formato Anthropic
    content_blocks = respuesta.get("content", [])
    stop_reason    = respuesta.get("stop_reason", "end_turn")

    # Sin tool calls → texto directo
    if stop_reason != "tool_use":
        for block in content_blocks:
            if block.get("type") == "text":
                return block.get("text", "No entendí eso. Intenta de nuevo.")
        return "No entendí eso. Intenta de nuevo."

    # Con tool calls → ejecutar cada una
    tool_results = []
    for block in content_blocks:
        if block.get("type") != "tool_use":
            continue
        nombre    = block["name"]
        args      = block.get("input", {})
        tool_id   = block["id"]
        logger.info("Ejecutando tool: %s args: %s", nombre, args)
        resultado = _ejecutar_tool(nombre, args, token)
        tool_results.append({
            "type":        "tool_result",
            "tool_use_id": tool_id,
            "content":     resultado,
        })

    # Ronda 2 — devolver resultados al modelo para respuesta final
    messages.append({"role": "assistant", "content": content_blocks})
    messages.append({"role": "user",      "content": tool_results})

    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{AIBASE_URL}/v1/messages",
            json={
                "model":      "luka",
                "messages":   messages,
                "max_tokens": 512,
                "system":     SYSTEM_PROMPT,
            },
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        respuesta_final = r.json()

    for block in respuesta_final.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "Listo.")
    return "Listo."