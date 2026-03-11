import logging
import random
import string

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

from app.core.config import settings

logger = logging.getLogger(__name__)


def _generar_codigo() -> str:
    return "".join(random.choices(string.digits, k=6))


def enviar_codigo(email: str, codigo: str, nombre: str = None) -> bool:
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = settings.brevo_api_key

    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(configuration)
    )

    destinatario = {"email": email}
    if nombre:
        destinatario["name"] = nombre

    mensaje = sib_api_v3_sdk.SendSmtpEmail(
        sender={
            "name": settings.brevo_from_name,
            "email": settings.brevo_from_email,
        },
        to=[destinatario],
        subject="Tu código de acceso a LUKA",
        html_content=f"""
        <div style="font-family: Arial, sans-serif; max-width: 400px; margin: 0 auto;">
            <h2 style="color: #00E676;">LUKA</h2>
            <p>Tu código de acceso es:</p>
            <div style="font-size: 36px; font-weight: bold; letter-spacing: 8px;
                        padding: 20px; background: #f5f5f5; text-align: center;
                        border-radius: 8px; margin: 20px 0;">
                {codigo}
            </div>
            <p style="color: #666;">Este código expira en <strong>10 minutos</strong>.</p>
            <p style="color: #666; font-size: 12px;">
                Si no solicitaste este código, ignora este mensaje.
            </p>
        </div>
        """,
    )

    try:
        api_instance.send_transac_email(mensaje)
        logger.info("Código enviado a %s", email)
        return True
    except ApiException as e:
        logger.error("Error enviando email a %s: %s", email, e)
        return False


def generar_y_enviar_codigo(email: str, nombre: str = None) -> str:
    codigo = _generar_codigo()
    if not enviar_codigo(email, codigo, nombre):
        raise RuntimeError("No se pudo enviar el código de verificación.")
    return codigo
