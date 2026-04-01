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
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

_MLX_BASE  = os.environ.get("MLX_SERVER_URL", "http://127.0.0.1:8181/luka")
AIBASE_URL = _MLX_BASE.rsplit("/luka", 1)[0]
API_URL    = os.environ.get("LUKA_API_URL", "http://luka-api:8000")

# MES_ACTUAL eliminado — nunca como constante de módulo, siempre en runtime


def _hoy() -> date:
    """Fecha actual en runtime — nunca como constante de módulo."""
    return date.today()


def _fecha_actual_str() -> str:
    return _hoy().strftime("%Y-%m-%d")


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

SYSTEM_RESPUESTA = """Eres LUKA, el asistente de finanzas personales para colombianos.
Responde SIEMPRE en español colombiano. Sé conciso y amigable.
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
# Parser de rango de fechas
# ─────────────────────────────────────────────────────────────────────────────

_DIAS_ES = {
    0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves",
    4: "viernes", 5: "sábado", 6: "domingo",
}

PROMPT_RANGO = """Hoy es {fecha_hoy} ({dia_semana}).
El usuario quiere un reporte. Analiza su mensaje y clasifica el período que menciona.

Responde ÚNICAMENTE con uno de estos formatos exactos, sin texto adicional:

DIA|0                            → hoy
DIA|1                            → ayer
SEMANA|0                         → esta semana (lunes a hoy)
SEMANA|1                         → la semana pasada (lunes a domingo anteriores)
MES|0                            → este mes (del 1 al día de hoy)
MES|1                            → el mes pasado (mes completo anterior)
MES|N                            → hace N meses (siendo N un número: 2, 3, etc.)
ANIO|0                           → este año (del 1 de enero a hoy)
ANIO|1                           → el año pasado (año completo anterior)
DIAS_N|N                         → últimos N días (siendo N un número: 3, 7, 15, etc.)
SEMANAS_N|N                      → últimas N semanas
MESES_N|N                        → últimos N meses
MES_NOMBRE|YYYY-MM               → un mes específico por nombre (ej: enero 2026 → 2026-01)
RANGO_EXACTO|YYYY-MM-DD|YYYY-MM-DD → rango con fechas exactas

Mensaje del usuario: {texto}"""


def _inferir_rango(texto: str) -> tuple[date, date]:
    """
    Le pregunta al modelo qué período menciona el usuario y devuelve (desde, hasta).
    El modelo solo clasifica — Python hace toda la aritmética de fechas.
    Si el modelo devuelve algo inesperado, cae en el mes actual como fallback.
    """
    hoy        = _hoy()
    dia_semana = _DIAS_ES[hoy.weekday()]

    with httpx.Client(timeout=20.0) as client:
        r = client.post(
            f"{AIBASE_URL}/v1/chat/completions",
            json={
                "model":    "luka",
                "messages": [{"role": "user", "content": PROMPT_RANGO.format(
                    fecha_hoy=hoy.strftime("%Y-%m-%d"),
                    dia_semana=dia_semana,
                    texto=texto,
                )}],
                "max_tokens": 20,
            },
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()

    raw = r.json()["choices"][0]["message"].get("content", "").strip()
    logger.info("Rango inferido por modelo: %r", raw)
    return _calcular_rango(raw, hoy)


def _calcular_rango(token: str, hoy: date) -> tuple[date, date]:
    """
    Convierte el token del modelo a (desde, hasta) usando hoy como ancla.
    Toda la aritmética de fechas vive aquí — el modelo nunca calcula nada.
    """
    partes = token.strip().split("|")
    tipo   = partes[0].upper() if partes else ""

    try:
        # ── Hoy ──────────────────────────────────────────────────────────────
        if tipo == "DIA" and partes[1] == "0":
            return hoy, hoy

        # ── Ayer ─────────────────────────────────────────────────────────────
        if tipo == "DIA" and partes[1] == "1":
            ayer = hoy - timedelta(days=1)
            return ayer, ayer

        # ── Esta semana (lunes a hoy) ─────────────────────────────────────────
        if tipo == "SEMANA" and partes[1] == "0":
            lunes = hoy - timedelta(days=hoy.weekday())
            return lunes, hoy

        # ── Semana pasada (lunes a domingo) ───────────────────────────────────
        if tipo == "SEMANA" and partes[1] == "1":
            lunes_esta  = hoy - timedelta(days=hoy.weekday())
            domingo_ant = lunes_esta - timedelta(days=1)
            lunes_ant   = domingo_ant - timedelta(days=6)
            return lunes_ant, domingo_ant

        # ── Este mes ──────────────────────────────────────────────────────────
        if tipo == "MES" and partes[1] == "0":
            return date(hoy.year, hoy.month, 1), hoy

        # ── Mes pasado ────────────────────────────────────────────────────────
        if tipo == "MES" and partes[1] == "1":
            primer_dia_este = date(hoy.year, hoy.month, 1)
            ultimo_mes_ant  = primer_dia_este - timedelta(days=1)
            return date(ultimo_mes_ant.year, ultimo_mes_ant.month, 1), ultimo_mes_ant

        # ── Hace N meses ──────────────────────────────────────────────────────
        if tipo == "MES" and len(partes) > 1 and partes[1].isdigit():
            n       = int(partes[1])
            inicio  = date(hoy.year, hoy.month, 1)
            for _ in range(n):
                inicio = (inicio - timedelta(days=1)).replace(day=1)
            if inicio.month == 12:
                fin = date(inicio.year, 12, 31)
            else:
                fin = date(inicio.year, inicio.month + 1, 1) - timedelta(days=1)
            return inicio, fin

        # ── Este año ──────────────────────────────────────────────────────────
        if tipo == "ANIO" and partes[1] == "0":
            return date(hoy.year, 1, 1), hoy

        # ── Año pasado ────────────────────────────────────────────────────────
        if tipo == "ANIO" and partes[1] == "1":
            return date(hoy.year - 1, 1, 1), date(hoy.year - 1, 12, 31)

        # ── Últimos N días ────────────────────────────────────────────────────
        if tipo == "DIAS_N":
            n = int(partes[1])
            return hoy - timedelta(days=n - 1), hoy

        # ── Últimas N semanas ─────────────────────────────────────────────────
        if tipo == "SEMANAS_N":
            n = int(partes[1])
            return hoy - timedelta(weeks=n), hoy

        # ── Últimos N meses ───────────────────────────────────────────────────
        if tipo == "MESES_N":
            n      = int(partes[1])
            inicio = date(hoy.year, hoy.month, 1)
            for _ in range(n):
                inicio = (inicio - timedelta(days=1)).replace(day=1)
            return inicio, hoy

        # ── Mes por nombre (YYYY-MM) ──────────────────────────────────────────
        if tipo == "MES_NOMBRE":
            d = date.fromisoformat(f"{partes[1]}-01")
            if d.month == 12:
                fin = date(d.year, 12, 31)
            else:
                fin = date(d.year, d.month + 1, 1) - timedelta(days=1)
            return d, fin

        # ── Rango exacto ──────────────────────────────────────────────────────
        if tipo == "RANGO_EXACTO":
            return date.fromisoformat(partes[1]), date.fromisoformat(partes[2])

    except (IndexError, ValueError) as e:
        logger.warning("No se pudo parsear token de rango %r: %s — usando mes actual", token, e)

    # Fallback: mes actual hasta hoy
    return date(hoy.year, hoy.month, 1), hoy


def _label_rango(desde: date, hasta: date) -> str:
    """Genera una etiqueta legible para mostrarle al usuario."""
    hoy = _hoy()

    if desde == hasta:
        if desde == hoy:
            return "hoy"
        if desde == hoy - timedelta(days=1):
            return "ayer"
        return desde.strftime("%d/%m/%Y")

    lunes_esta = hoy - timedelta(days=hoy.weekday())
    if desde == lunes_esta and hasta == hoy:
        return "esta semana"

    lunes_ant   = lunes_esta - timedelta(days=7)
    domingo_ant = lunes_esta - timedelta(days=1)
    if desde == lunes_ant and hasta == domingo_ant:
        return "semana pasada"

    if desde == date(hoy.year, hoy.month, 1) and hasta == hoy:
        return "este mes"

    if desde == date(hoy.year, 1, 1) and hasta == hoy:
        return "este año"

    if desde.year == hasta.year and desde.month == hasta.month:
        return desde.strftime("%B %Y")

    return f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"


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


def _accion_reporte(texto: str, token: str) -> tuple[str, date, date]:
    """
    Infiere el rango de fechas y obtiene datos del reporte.
    Devuelve (contexto_str, desde, hasta).
    """
    desde, hasta = _inferir_rango(texto)

    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{API_URL}/reportes/rango",
            params={"desde": desde.isoformat(), "hasta": hasta.isoformat()},
            headers=_headers(token),
        )
        r.raise_for_status()
        data = r.json()

    label = _label_rango(desde, hasta)

    if not data:
        return f"No hay gastos registrados para {label}.", desde, hasta

    total = sum(float(item["total"]) for item in data)
    lines = [f"Reporte {label} ({desde} al {hasta}):"]
    for item in data:
        lines.append(f"- {item['categoria']}: ${float(item['total']):,.0f}")
    lines.append(f"Total: ${total:,.0f}")
    return "\n".join(lines), desde, hasta


# ─────────────────────────────────────────────────────────────────────────────
# Respuesta final via modelo
# ─────────────────────────────────────────────────────────────────────────────

def _respuesta_modelo(texto_usuario: str, contexto: str) -> str:
    """Llama al modelo con el contexto ya resuelto para que genere respuesta natural."""
    system = SYSTEM_RESPUESTA + f"\nFECHA ACTUAL: {_fecha_actual_str()}\n"
    prompt = f"Pregunta del usuario: {texto_usuario}\n\nDatos disponibles:\n{contexto}"

    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{AIBASE_URL}/v1/chat/completions",
            json={
                "model":    "luka",
                "messages": [
                    {"role": "system", "content": system},
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
        contexto, desde, hasta = _accion_reporte(texto, token)
        label                  = _label_rango(desde, hasta)
        respuesta              = _respuesta_modelo(
            f"{texto} [período: {label}, {desde} al {hasta}]",
            contexto,
        )
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