"""
bot/main.py — Bot de Telegram para LUKA
========================================
Corre en Docker como luka-bot.
Se comunica con luka-api via HTTP.
"""
import logging
import os
import re
from datetime import datetime

import httpx
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.agent import agente_luka

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
API_URL        = os.environ.get("LUKA_API_URL", "http://luka-api:8000")

VINCULAR_EMAIL  = 1
VINCULAR_CODIGO = 2

KEY_TOKEN   = "jwt_token"
KEY_ULTIMOS = "ultimos_gastos"
KEY_PREVIEW = "preview_data"
KEY_BORRAR_PENDIENTE = "borrar_pendiente"
KEY_GASTO_PENDIENTE = "gasto_pendiente"

COMANDOS_TEXTO = (
    "Comandos disponibles:\n"
    "/vincular — conecta tu cuenta LUKA\n"
    "/desvincular — desconecta tu Telegram (historial intacto)\n"
    "/gasto — registra un gasto manual\n"
    "/reporte — resumen del mes actual\n"
    "/reporte AAAA-MM — resumen de un mes específico\n"
    "/ultimos — últimos 5 gastos\n"
    "/borrar — elimina un gasto de la lista anterior\n\n"
    "También puedes enviarme una *foto de un recibo* y lo registro automáticamente. 📸\n"
    "O simplemente cuéntame qué gastaste en tus propias palabras. 💬"
)

# ─────────────────────────────────────────────────────────────────────────────
# Cliente HTTP
# ─────────────────────────────────────────────────────────────────────────────
def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def _get(path: str, token: str) -> dict:
    with httpx.Client(timeout=120.0) as client:
        r = client.get(f"{API_URL}{path}", headers=_headers(token))
        r.raise_for_status()
        return r.json()

def _post(path: str, payload: dict, token: str = None) -> dict:
    headers = _headers(token) if token else {"Content-Type": "application/json"}
    with httpx.Client(timeout=120.0) as client:
        r = client.post(f"{API_URL}{path}", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

def _delete(path: str, token: str) -> dict:
    with httpx.Client(timeout=30.0) as client:
        r = client.delete(f"{API_URL}{path}", headers=_headers(token))
        r.raise_for_status()
        return r.json()

def _post_file(path: str, file_bytes: bytes, token: str, mime: str = "image/jpeg") -> dict:
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"{API_URL}{path}",
            files={"file": ("foto.jpg", file_bytes, mime)},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _recuperar_token(telegram_id: int) -> str | None:
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{API_URL}/auth/token-telegram/{telegram_id}")
            if r.status_code == 200:
                return r.json()["access_token"]
    except Exception:
        pass
    return None

def _token_o_recuperar(context: ContextTypes.DEFAULT_TYPE, telegram_id: int) -> str | None:
    token = context.user_data.get(KEY_TOKEN)
    if not token:
        token = _recuperar_token(telegram_id)
        if token:
            context.user_data[KEY_TOKEN] = token
    return token

def _formatear_categorias(categorias: dict) -> str:
    lineas = []
    total = 0
    for cat, monto in categorias.items():
        lineas.append(f"  • {cat}: ${float(monto):,.0f}")
        total += float(monto)
    lineas.append(f"\n💰 *Total: ${total:,.0f}*")
    return "\n".join(lineas)

def _formatear_preview_texto(data: dict) -> str:
    lines = ["📋 *Esto es lo que detecté:*\n"]
    if data.get("comercio"):
        lines.append(f"🏪 Comercio: {data['comercio']}")
    if data.get("fecha"):
        lines.append(f"📅 Fecha: {data['fecha']}")
    lines.append("\n📊 Categorías:")
    lines.append(_formatear_categorias(data["categorias"]))
    return "\n".join(lines)

def _enviar_respuesta_agente(texto: str) -> str:
    """Limpia el markdown del modelo para que Telegram lo renderice bien."""
    # Convertir **texto** → <b>texto</b>  y  *texto* → <i>texto</i>
    texto = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', texto)
    texto = re.sub(r'\*(.+?)\*',     r'<i>\1</i>', texto)
    return texto

# ─────────────────────────────────────────────────────────────────────────────
# Handlers — sin cambios respecto al original
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hola, soy *LUKA* — tu asistente de finanzas personales.\n\n"
        "Para comenzar, vincula tu cuenta con /vincular\n\n" + COMANDOS_TEXTO,
        parse_mode="Markdown",
    )

async def cmd_desconocido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ No reconozco ese comando.\n\n" + COMANDOS_TEXTO,
        parse_mode="Markdown",
    )

async def handle_texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = _token_o_recuperar(context, update.effective_user.id)
    if not token:
        await update.message.reply_text("⚠️ Primero vincula tu cuenta con /vincular")
        return

    texto = update.message.text.strip()
    if not texto:
        return

    await update.message.reply_text("⏳ Procesando...")
    try:
        resultado = agente_luka(texto, token)

        if resultado["tipo"] == "confirmar_borrado":
            context.user_data[KEY_BORRAR_PENDIENTE] = {
                "id":          resultado["id"],
                "descripcion": resultado["descripcion"],
                "monto":       resultado["monto"],
            }
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Confirmar", callback_data="confirmar_borrado_agente"),
                InlineKeyboardButton("❌ Cancelar",  callback_data="cancelar_borrado_agente"),
            ]])
            await update.message.reply_text(
                f"🗑️ ¿Eliminar *{resultado['descripcion']}* — ${resultado['monto']:,.0f}?",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        elif resultado["tipo"] == "confirmar_gasto":
            context.user_data[KEY_GASTO_PENDIENTE] = resultado["preview"]
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Guardar",   callback_data="confirmar_gasto_agente"),
                InlineKeyboardButton("❌ Cancelar",  callback_data="cancelar_gasto_agente"),
            ]])
            await update.message.reply_text(
                _enviar_respuesta_agente(resultado["respuesta"]),
                parse_mode="HTML",
                reply_markup=keyboard,
            )            
        else:
            await update.message.reply_text(
                _enviar_respuesta_agente(resultado["respuesta"]),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error("Error en agente_luka: %s", e)
        await update.message.reply_text("❌ No pude procesar eso. Intenta de nuevo.")

async def cmd_vincular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = _token_o_recuperar(context, update.effective_user.id)
    if token:
        await update.message.reply_text(
            "✅ Ya tienes una cuenta vinculada.\n"
            "Si deseas cambiarla, usa primero /desvincular."
        )
        return ConversationHandler.END
    await update.message.reply_text(
        "📧 Ingresa tu email para recibir un código de acceso.\n"
        "Si no tienes cuenta, se creará automáticamente."
    )
    return VINCULAR_EMAIL

async def vincular_recibir_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip().lower()
    context.user_data["email_vincular"] = email
    try:
        _post("/auth/solicitar-codigo", {"email": email, "nombre": update.effective_user.first_name})
        await update.message.reply_text(
            f"✉️ Código enviado a *{email}*.\nIngresa el código de 6 dígitos:",
            parse_mode="Markdown",
        )
        return VINCULAR_CODIGO
    except Exception as e:
        logger.error("Error en vincular_recibir_email: %s", e)
        await update.message.reply_text("❌ Error enviando el código. Intenta de nuevo.")
        return ConversationHandler.END

async def vincular_recibir_codigo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    codigo = update.message.text.strip()
    email  = context.user_data.get("email_vincular")
    try:
        resultado = _post("/auth/verificar-codigo", {"email": email, "codigo": codigo})
        token = resultado["access_token"]
        _post(
            "/auth/vincular-telegram",
            {
                "telegram_id":       update.effective_user.id,
                "username_telegram": update.effective_user.username,
            },
            token=token,
        )
        context.user_data[KEY_TOKEN] = token
        await update.message.reply_text(
            "✅ ¡Cuenta vinculada exitosamente!\n\n"
            "Ya puedes usar /gasto para registrar gastos.",
        )
    except Exception as e:
        logger.error("Error en vincular_recibir_codigo: %s", e)
        await update.message.reply_text("❌ Código inválido o expirado. Intenta con /vincular nuevamente.")
    return ConversationHandler.END

async def vincular_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Vinculación cancelada.")
    return ConversationHandler.END

async def cmd_desvincular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = _token_o_recuperar(context, update.effective_user.id)
    if not token:
        await update.message.reply_text("⚠️ No tienes ninguna cuenta vinculada.")
        return
    try:
        _delete("/auth/desvincular-telegram", token)
        context.user_data.pop(KEY_TOKEN, None)
        context.user_data.pop(KEY_ULTIMOS, None)
        context.user_data.pop(KEY_PREVIEW, None)
        await update.message.reply_text(
            "✅ Telegram desvinculado exitosamente.\n\n"
            "Tu historial de gastos sigue intacto.\n"
            "Usa /vincular para conectar este u otro Telegram a cualquier cuenta."
        )
    except Exception as e:
        logger.error("Error en cmd_desvincular: %s", e)
        await update.message.reply_text("❌ No se pudo desvincular. Intenta de nuevo.")

async def cmd_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = _token_o_recuperar(context, update.effective_user.id)
    if not token:
        await update.message.reply_text("⚠️ Primero vincula tu cuenta con /vincular")
        return
    descripcion = " ".join(context.args) if context.args else None
    if not descripcion:
        await update.message.reply_text(
            "📝 Uso: /gasto <descripcion>\n"
            "Ejemplo: /gasto gasté 5mil en pan y 3mil en gaseosa"
        )
        return
    await update.message.reply_text("⏳ Procesando...")
    try:
        gastos = _post("/gastos/manual", {"canal": "telegram", "descripcion": descripcion}, token=token)
        lines = ["✅ *Gastos registrados:*\n"]
        for g in gastos:
            lines.append(f"  • {g['categoria']}: ${float(g['monto']):,.0f} — {g['descripcion']}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error("Error en cmd_gasto: %s", e)
        await update.message.reply_text("❌ No se pudo registrar el gasto. Intenta de nuevo.")

async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = _token_o_recuperar(context, update.effective_user.id)
    if not token:
        await update.message.reply_text("⚠️ Primero vincula tu cuenta con /vincular")
        return
    await update.message.reply_text("⏳ Analizando la imagen...")
    try:
        photo  = update.message.photo[-1]
        tfile  = await photo.get_file()
        fbytes = await tfile.download_as_bytearray()
        preview = _post_file("/facturas/foto/preview", bytes(fbytes), token)
        context.user_data[KEY_PREVIEW] = preview
        texto = _formatear_preview_texto(preview)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Guardar", callback_data="confirmar_foto"),
            InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_preview"),
        ]])
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        logger.error("Error en handle_foto: %s", e)
        await update.message.reply_text("❌ No se pudo procesar la imagen. Intenta de nuevo.")

async def callback_confirmar_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    token   = _token_o_recuperar(context, update.effective_user.id)
    preview = context.user_data.get(KEY_PREVIEW)
    if not token or not preview:
        await query.edit_message_text("❌ Sesión expirada. Intenta nuevamente.")
        return
    try:
        _post("/facturas/foto/confirmar", preview, token=token)
        await query.edit_message_text("✅ ¡Guardado exitosamente!")
    except Exception as e:
        logger.error("Error en callback_confirmar_foto: %s", e)
        await query.edit_message_text("❌ No se pudo guardar. Intenta de nuevo.")
    finally:
        context.user_data.pop(KEY_PREVIEW, None)

async def callback_confirmar_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    token   = _token_o_recuperar(context, update.effective_user.id)
    preview = context.user_data.get(KEY_PREVIEW)
    if not token or not preview:
        await query.edit_message_text("❌ Sesión expirada. Intenta nuevamente.")
        return
    try:
        _post("/facturas/texto/confirmar", preview, token=token)
        await query.edit_message_text("✅ ¡Guardado exitosamente!")
    except Exception as e:
        logger.error("Error en callback_confirmar_texto: %s", e)
        await query.edit_message_text("❌ No se pudo guardar. Intenta de nuevo.")
    finally:
        context.user_data.pop(KEY_PREVIEW, None)

async def callback_cancelar_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop(KEY_PREVIEW, None)
    await query.edit_message_text("❌ Cancelado. No se guardó nada.")

async def callback_confirmar_borrado_agente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    token    = _token_o_recuperar(context, update.effective_user.id)
    pendiente = context.user_data.get(KEY_BORRAR_PENDIENTE)
    if not token or not pendiente:
        await query.edit_message_text("❌ Sesión expirada. Intenta nuevamente.")
        return
    try:
        with httpx.Client(timeout=30.0) as client:
            # intentar gasto manual primero, luego factura
            r = client.delete(
                f"{API_URL}/gastos/manual/{pendiente['id']}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 404:
                r = client.delete(
                    f"{API_URL}/facturas/{pendiente['id']}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            r.raise_for_status()
        await query.edit_message_text(
            f"✅ Eliminado: {pendiente['descripcion']} — ${pendiente['monto']:,.0f}"
        )
    except Exception as e:
        logger.error("Error en callback_confirmar_borrado_agente: %s", e)
        await query.edit_message_text("❌ No se pudo eliminar. Intenta de nuevo.")
    finally:
        context.user_data.pop(KEY_BORRAR_PENDIENTE, None)

async def callback_cancelar_borrado_agente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop(KEY_BORRAR_PENDIENTE, None)
    await query.edit_message_text("❌ Cancelado. No se eliminó nada.")

async def callback_confirmar_gasto_agente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    token   = _token_o_recuperar(context, update.effective_user.id)
    preview = context.user_data.get(KEY_GASTO_PENDIENTE)
    if not token or not preview:
        await query.edit_message_text("❌ Sesión expirada. Intenta nuevamente.")
        return
    try:
        # Guardar cada categoría como gasto manual independiente
        for cat, monto in preview["categorias"].items():
            desc = preview.get("descripciones", {}).get(cat) or preview.get("raw_text", cat)
            _post(
                "/gastos/manual",
                {
                    "canal":       "telegram",
                    "descripcion": desc,
                    "categoria":   cat,
                    "monto":       monto,
                },
                token=token,
            )
        await query.edit_message_text("✅ ¡Gasto guardado exitosamente!")
    except Exception as e:
        logger.error("Error en callback_confirmar_gasto_agente: %s", e)
        await query.edit_message_text("❌ No se pudo guardar. Intenta de nuevo.")
    finally:
        context.user_data.pop(KEY_GASTO_PENDIENTE, None)

async def callback_cancelar_gasto_agente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop(KEY_GASTO_PENDIENTE, None)
    await query.edit_message_text("❌ Cancelado. No se guardó nada.")

async def cmd_reporte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = _token_o_recuperar(context, update.effective_user.id)
    if not token:
        await update.message.reply_text("⚠️ Primero vincula tu cuenta con /vincular")
        return
    mes = context.args[0] if context.args else None
    path = f"/reportes/mensual?mes={mes}" if mes else "/reportes/mensual"
    try:
        data = _get(path, token)
        if not data:
            await update.message.reply_text("📭 No hay gastos registrados para ese período.")
            return
        mes_label = data[0]["mes"] if data else mes or datetime.now().strftime("%Y-%m")
        lines = [f"📊 *Reporte {mes_label}:*\n"]
        total_general = 0
        for item in data:
            lines.append(f"  • {item['categoria']}: ${float(item['total']):,.0f}")
            total_general += float(item["total"])
        lines.append(f"\n💰 *Total: ${total_general:,.0f}*")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error("Error en cmd_reporte: %s", e)
        await update.message.reply_text("❌ No se pudo obtener el reporte. Intenta de nuevo.")

async def cmd_ultimos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = _token_o_recuperar(context, update.effective_user.id)
    if not token:
        await update.message.reply_text("⚠️ Primero vincula tu cuenta con /vincular")
        return
    try:
        gastos   = _get("/gastos/manual", token)
        facturas = _get("/facturas/", token)

        combinados = []
        for g in gastos:
            combinados.append({**g, "_tipo": "gasto"})
        for f in facturas:
            combinados.append({
                "id":          f["id"],
                "descripcion": f.get("comercio") or "Factura sin comercio",
                "monto":       f.get("total") or 0,
                "categoria":   "FACTURA",
                "fecha":       f.get("fecha_factura") or f.get("creado_en", "")[:10],
                "_tipo":       "factura",
            })

        combinados.sort(key=lambda x: x.get("fecha", ""), reverse=True)
        ultimos = combinados[:5]

        if not ultimos:
            await update.message.reply_text("📭 No tienes gastos registrados.")
            context.user_data[KEY_ULTIMOS] = []
            return

        context.user_data[KEY_ULTIMOS] = ultimos
        lines = ["📋 *Últimos registros:*\n"]
        for i, r in enumerate(ultimos, 1):
            icono = "🧾" if r["_tipo"] == "factura" else "💸"
            lines.append(
                f"  *{i}.* {icono} {r['descripcion']} — ${float(r['monto']):,.0f}\n"
                f"      {r['fecha']}"
            )
        lines.append("\nUsa /borrar <número> para eliminar uno.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error("Error en cmd_ultimos: %s", e)
        await update.message.reply_text("❌ No se pudieron obtener los últimos registros. Intenta de nuevo.")

async def cmd_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = _token_o_recuperar(context, update.effective_user.id)
    if not token:
        await update.message.reply_text("⚠️ Primero vincula tu cuenta con /vincular")
        return
    ultimos = context.user_data.get(KEY_ULTIMOS)
    if not ultimos:
        await update.message.reply_text("⚠️ Primero ejecuta /ultimos para ver tus registros recientes.")
        return
    if not context.args:
        await update.message.reply_text("📝 Uso: /borrar <número>\nEjemplo: /borrar 2")
        return
    try:
        numero = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ El número debe ser un entero. Ejemplo: /borrar 2")
        return
    if numero < 1 or numero > len(ultimos):
        await update.message.reply_text(f"❌ Número inválido. Elige entre 1 y {len(ultimos)}.")
        return

    registro = ultimos[numero - 1]
    tipo     = registro["_tipo"]
    path     = f"/facturas/{registro['id']}" if tipo == "factura" else f"/gastos/manual/{registro['id']}"

    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.delete(f"{API_URL}{path}", headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
        context.user_data[KEY_ULTIMOS] = None
        icono = "🧾" if tipo == "factura" else "💸"
        await update.message.reply_text(
            f"✅ Eliminado: {icono} {registro['descripcion']} — ${float(registro['monto']):,.0f}"
        )
    except Exception as e:
        logger.error("Error en cmd_borrar: %s", e)
        await update.message.reply_text("❌ No se pudo eliminar el registro. Intenta de nuevo.")

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start",       "Inicio y ayuda"),
        BotCommand("vincular",    "Conecta tu cuenta LUKA"),
        BotCommand("desvincular", "Desconecta tu Telegram (historial intacto)"),
        BotCommand("gasto",       "Registra un gasto manual"),
        BotCommand("reporte",     "Resumen del mes o uno específico: /reporte 2026-03"),
        BotCommand("ultimos",     "Últimos 5 gastos"),
        BotCommand("borrar",      "Elimina un gasto"),
    ])

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    vincular_conv = ConversationHandler(
        entry_points=[CommandHandler("vincular", cmd_vincular)],
        states={
            VINCULAR_EMAIL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, vincular_recibir_email)],
            VINCULAR_CODIGO: [MessageHandler(filters.TEXT & ~filters.COMMAND, vincular_recibir_codigo)],
        },
        fallbacks=[CommandHandler("cancelar", vincular_cancelar)],
    )

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(vincular_conv)
    app.add_handler(CommandHandler("desvincular", cmd_desvincular))
    app.add_handler(CommandHandler("gasto",       cmd_gasto))
    app.add_handler(CommandHandler("reporte",     cmd_reporte))
    app.add_handler(CommandHandler("ultimos",     cmd_ultimos))
    app.add_handler(CommandHandler("borrar",      cmd_borrar))
    app.add_handler(CallbackQueryHandler(callback_confirmar_foto,   pattern="^confirmar_foto$"))
    app.add_handler(CallbackQueryHandler(callback_confirmar_texto,  pattern="^confirmar_texto$"))
    app.add_handler(CallbackQueryHandler(callback_cancelar_preview, pattern="^cancelar_preview$"))
    app.add_handler(CallbackQueryHandler(callback_confirmar_borrado_agente, pattern="^confirmar_borrado_agente$"))
    app.add_handler(CallbackQueryHandler(callback_cancelar_borrado_agente,  pattern="^cancelar_borrado_agente$"))
    app.add_handler(CallbackQueryHandler(callback_confirmar_gasto_agente, pattern="^confirmar_gasto_agente$"))
    app.add_handler(CallbackQueryHandler(callback_cancelar_gasto_agente,  pattern="^cancelar_gasto_agente$"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_foto))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_texto_libre))
    app.add_handler(MessageHandler(filters.COMMAND, cmd_desconocido))

    logger.info("Bot LUKA iniciado.")
    app.run_polling()

if __name__ == "__main__":
    main()