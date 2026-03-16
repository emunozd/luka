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

_TRIGGERS_GASTO = (
    "pagué", "pague", "compré", "compre", "gasté", "gaste",
    "me costó", "me costo", "invertí", "inverti", "me cobró",
    "me cobro", "desembolsé", "desembolse", "salió", "salio",
    "vale", "valió", "valio", "costó", "costo",
)

SYSTEM_PROMPT = f"""Eres LUKA, el asistente de finanzas personales para colombianos.
El usuario te habla por Telegram en lenguaje natural. Tu trabajo es entender qué quiere y ejecutar la acción correcta.

FECHA ACTUAL: {MES_ACTUAL}

ACCIONES DISPONIBLES — solo puedes hacer estas cuatro cosas:
1. Registrar un gasto → usa registrar_gasto
2. Ver reporte del mes → usa ver_reporte
3. Ver últimos gastos → usa ver_ultimos
4. Borrar un gasto → usa borrar_gasto (o formato BORRAR_PENDIENTE si necesitas confirmación)

REGLAS:
- Si el usuario menciona un gasto (pagué, compré, gasté, me costó, etc.) → usa registrar_gasto INMEDIATAMENTE.
- Si el usuario quiere saber cuánto ha gastado, un resumen o reporte (de cualquier mes, incluyendo meses anteriores) → usa ver_reporte INMEDIATAMENTE. Puedes consultar cualquier mes histórico, no solo el actual.
- Si el usuario menciona 'últimos pagos', 'últimos gastos', 'gastos recientes', 'mis gastos', 'mis pagos', 'qué he gastado', 'historial', 'movimientos', 'transacciones', o cualquier variación → usa ver_ultimos INMEDIATAMENTE sin preguntar nada ni responder texto.
- Si el usuario quiere borrar o eliminar un registro y ya tienes el ID → responde ÚNICAMENTE con este formato exacto: BORRAR_PENDIENTE|<id>|<descripcion>|<monto>
- Si el usuario quiere borrar pero no sabes cuál → llama ver_ultimos primero, luego usa el formato BORRAR_PENDIENTE con el ID correcto.
- Si no es ninguna de las anteriores → responde directamente en texto, amigable y breve.
- Si la pregunta NO está relacionada con ninguna de las 4 acciones anteriores → responde ÚNICAMENTE con: FUERA_DE_SCOPE
- NUNCA respondas preguntas generales, de conocimiento, noticias, precios de activos, clima, ni nada que no sea finanzas personales del usuario.
- NUNCA respondas en inglés. SIEMPRE en español colombiano.
- NUNCA respondas con palabras técnicas ni nombres de funciones(ver_ultimos, registrar_gasto, ver_reporte, etc.). Si no puedes ejecutar una acción, di simplemente que no entendiste.
- NUNCA pidas clarificación si la intención es clara. Actúa directamente.
- Sé conciso. Máximo 3 líneas cuando no hay datos que mostrar.
- En el formato BORRAR_PENDIENTE el monto debe ser un número puro sin símbolo ni comas. Ejemplo: BORRAR_PENDIENTE|uuid|Jumbo|490664
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
                        "description": (
                            "Mes en formato YYYY-MM. Puede ser cualquier mes histórico. "
                            "Si el usuario dice 'mes pasado' calcula el mes anterior al actual ({MES_ACTUAL}). "
                            "Si no especifica mes, usa el actual: {MES_ACTUAL}."
                        ),
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

def _es_gasto_directo(texto: str) -> bool:
    texto_lower = texto.lower()
    return any(t in texto_lower for t in _TRIGGERS_GASTO)

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
    """Llama al endpoint de preview (categoriza sin guardar) y devuelve el resultado."""
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"{API_URL}/facturas/texto/preview",
            json={"texto": descripcion},
            headers=_headers(token),
        )
        r.raise_for_status()
        preview = r.json()
    if not preview.get("categorias"):
        return "No se pudo categorizar el gasto."
    lines = ["PREVIEW_GASTO"]
    lines.append(f"raw_text={descripcion}")
    for cat, monto in preview["categorias"].items():
        lines.append(f"{cat}:{monto}")
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
    ultimos = combinados[:5]

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


def _tool_borrar_gasto(gasto_id: str, token: str) -> str:
    # Intentar primero como gasto manual, luego como factura
    with httpx.Client(timeout=30.0) as client:
        r = client.delete(
            f"{API_URL}/gastos/manual/{gasto_id}",
            headers=_headers(token),
        )
        if r.status_code == 404:
            r = client.delete(
                f"{API_URL}/facturas/{gasto_id}",
                headers=_headers(token),
            )
        r.raise_for_status()
    return "Registro eliminado correctamente."


# ─────────────────────────────────────────────────────────────────────────────
# Loop del agente
# ─────────────────────────────────────────────────────────────────────────────

def agente_luka(texto: str, token: str) -> dict:
    """
    Retorna:
      {"tipo": "texto", "respuesta": str}
      {"tipo": "confirmar_borrado", "respuesta": str, "id": str, "descripcion": str, "monto": float}
      {"tipo": "confirmar_gasto", "respuesta": str, "preview": dict}
    """
    messages = [{"role": "user", "content": texto}]

    # Si el texto claramente es un gasto, forzar la tool directamente
    tool_choice = (
        {"type": "tool", "name": "registrar_gasto"}
        if _es_gasto_directo(texto)
        else {"type": "auto"}
    )


    with httpx.Client(timeout=120.0) as client:
        # Ronda 1 — usar /v1/messages (Anthropic-compatible, tool calling funcional)
        r = client.post(
            f"{AIBASE_URL}/v1/messages",
            json={
                "model":       "luka",
                "messages":    messages,
                "tools":       TOOLS,
                "tool_choice": tool_choice,
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
                texto_bloque = block.get("text", "").strip()
                if texto_bloque == "FUERA_DE_SCOPE":
                    return {"tipo": "texto", "respuesta": "Solo puedo ayudarte con tus finanzas personales: registrar gastos, ver reportes o revisar tu historial. 💸"}
                return {"tipo": "texto", "respuesta": texto_bloque}
        return {"tipo": "texto", "respuesta": "No entendí eso. Intenta de nuevo."}

    # Con tool calls → ejecutar cada una
    tool_results = []
    preview_gasto = None
    for block in content_blocks:
        if block.get("type") != "tool_use":
            continue
        nombre    = block["name"]
        args      = block.get("input", {})
        tool_id   = block["id"]
        logger.info("Ejecutando tool: %s args: %s", nombre, args)
        resultado = _ejecutar_tool(nombre, args, token)

         # Interceptar preview de gasto antes de ir a ronda 2
        if nombre == "registrar_gasto" and resultado.startswith("PREVIEW_GASTO"):
            lineas = resultado.split("\n")
            raw_text = next((l.split("=", 1)[1] for l in lineas if l.startswith("raw_text=")), "")
            categorias = {}
            for l in lineas[2:]:
                if ":" in l:
                    cat, monto = l.split(":", 1)
                    categorias[cat] = float(monto)
            preview_gasto = {"texto": raw_text, "categorias": categorias}
            resultado = "Preview generado correctamente."

        tool_results.append({
            "type":        "tool_result",
            "tool_use_id": tool_id,
            "content":     resultado,
        })

        # Si hay preview de gasto → retornar para confirmación sin ir a ronda 2
    if preview_gasto:
        total = sum(preview_gasto["categorias"].values())
        lineas_preview = ["Esto es lo que voy a registrar:"]
        for cat, monto in preview_gasto["categorias"].items():
            lineas_preview.append(f"- {cat}: ${float(monto):,.0f}")
        lineas_preview.append(f"Total: ${total:,.0f}")
        return {
            "tipo":        "confirmar_gasto",
            "respuesta":   "\n".join(lineas_preview),
            "preview":     preview_gasto,
        }

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
            texto = block.get("text", "").strip()
            if texto == "FUERA_DE_SCOPE":
                return {"tipo": "texto", "respuesta": "Solo puedo ayudarte con tus finanzas personales: registrar gastos, ver reportes o revisar tu historial. 💸"}
            if texto.startswith("BORRAR_PENDIENTE|"):
                partes = texto.split("|")
                monto_raw = partes[3].replace("$", "").replace(",", "").strip()
                return {
                    "tipo":        "confirmar_borrado",
                    "id":          partes[1],
                    "descripcion": partes[2],
                    "monto":       float(monto_raw),
                    "respuesta":   f"Listo, voy a eliminar {partes[2]} — ${float(monto_raw):,.0f}",
                }
            return {"tipo": "texto", "respuesta": texto}
    return {"tipo": "texto", "respuesta": "Listo."}