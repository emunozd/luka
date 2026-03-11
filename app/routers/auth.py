from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.core.database import get_db
from app.core.deps import get_usuario_actual
from app.core.security import crear_token
from app.models.models import CodigoVerificacion, TelegramCuenta, Usuario
from app.schemas.schemas import SolicitarCodigoRequest, TokenOut, UsuarioOut, VerificarCodigoRequest
from app.services.email_service import generar_y_enviar_codigo

router = APIRouter(prefix="/auth", tags=["auth"])

EXPIRACION_MINUTOS = 10


@router.post("/solicitar-codigo", status_code=status.HTTP_200_OK)
async def solicitar_codigo(
    payload: SolicitarCodigoRequest,
    db: AsyncSession = Depends(get_db),
):
    email = payload.email.strip().lower()
    result = await db.execute(select(Usuario).where(Usuario.email == email))
    usuario = result.scalar_one_or_none()
    if not usuario and not payload.nombre:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe una cuenta con ese email. Incluye tu nombre para registrarte.",
        )
    if not usuario:
        usuario = Usuario(nombre=payload.nombre, email=email)
        db.add(usuario)
        await db.flush()
    try:
        codigo = generar_y_enviar_codigo(email, usuario.nombre)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    expira = datetime.now(timezone.utc) + timedelta(minutes=EXPIRACION_MINUTOS)
    db.add(CodigoVerificacion(
        email=email,
        codigo=codigo,
        expira_en=expira,
    ))
    await db.commit()
    return {"mensaje": f"Código enviado a {email}. Expira en {EXPIRACION_MINUTOS} minutos."}


@router.post("/verificar-codigo", response_model=TokenOut)
async def verificar_codigo(
    payload: VerificarCodigoRequest,
    db: AsyncSession = Depends(get_db),
):
    email = payload.email.strip().lower()
    ahora = datetime.now(timezone.utc)
    result = await db.execute(
        select(CodigoVerificacion).where(
            CodigoVerificacion.email == email,
            CodigoVerificacion.codigo == payload.codigo,
            CodigoVerificacion.usado == False,
            CodigoVerificacion.expira_en > ahora,
        )
    )
    codigo_obj = result.scalar_one_or_none()
    if not codigo_obj:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Código inválido o expirado.",
        )
    codigo_obj.usado = True
    result = await db.execute(select(Usuario).where(Usuario.email == email))
    usuario = result.scalar_one_or_none()
    if not usuario or not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo.",
        )
    await db.commit()
    return TokenOut(access_token=crear_token(str(usuario.id)))


@router.get("/me", response_model=UsuarioOut)
async def me(usuario: Usuario = Depends(get_usuario_actual)):
    return usuario


class VincularTelegramRequest(BaseModel):
    telegram_id: int
    username_telegram: Optional[str] = None


@router.post("/vincular-telegram", status_code=status.HTTP_200_OK)
async def vincular_telegram(
    payload: VincularTelegramRequest,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    # Verificar si este telegram_id ya está vinculado a OTRO usuario
    result = await db.execute(
        select(TelegramCuenta).where(TelegramCuenta.telegram_id == payload.telegram_id)
    )
    cuenta_existente = result.scalar_one_or_none()
    if cuenta_existente and cuenta_existente.usuario_id != usuario.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este Telegram ya está vinculado a otra cuenta. Usa /desvincular primero.",
        )

    # Buscar si este usuario ya tiene un telegram vinculado
    result = await db.execute(
        select(TelegramCuenta).where(TelegramCuenta.usuario_id == usuario.id)
    )
    cuenta = result.scalar_one_or_none()
    if cuenta:
        cuenta.telegram_id = payload.telegram_id
        cuenta.username_telegram = payload.username_telegram
    else:
        db.add(TelegramCuenta(
            usuario_id=usuario.id,
            telegram_id=payload.telegram_id,
            username_telegram=payload.username_telegram,
        ))
    await db.commit()
    return {"mensaje": "Telegram vinculado exitosamente."}


@router.get("/token-telegram/{telegram_id}", response_model=TokenOut)
async def token_por_telegram(
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(TelegramCuenta).where(TelegramCuenta.telegram_id == telegram_id)
    )
    cuenta = result.scalar_one_or_none()
    if not cuenta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Telegram no vinculado.",
        )
    result = await db.execute(
        select(Usuario).where(Usuario.id == cuenta.usuario_id, Usuario.activo == True)
    )
    usuario = result.scalar_one_or_none()
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado o inactivo.",
        )
    return TokenOut(access_token=crear_token(str(usuario.id)))


@router.delete("/desvincular-telegram", status_code=status.HTTP_200_OK)
async def desvincular_telegram(
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    result = await db.execute(
        select(TelegramCuenta).where(TelegramCuenta.usuario_id == usuario.id)
    )
    cuenta = result.scalar_one_or_none()
    if not cuenta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No hay ningún Telegram vinculado a esta cuenta.",
        )
    await db.delete(cuenta)
    await db.commit()
    return {"mensaje": "Telegram desvinculado. Tu historial sigue intacto. Usa /vincular para conectar otro correo o Telegram."}
