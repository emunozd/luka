import base64
from datetime import date
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.core.database import get_db
from app.core.deps import get_usuario_actual
from app.models.models import Factura, GastoManual, ResumenCategoria, Usuario
from app.schemas.schemas import FacturaOut, FacturaTextoCreate
from app.services.ai_client import categorizar_factura_imagen, categorizar_factura_texto

router = APIRouter(prefix="/facturas", tags=["facturas"])

MIME_PERMITIDOS = {"image/jpeg", "image/png", "image/webp", "image/heic"}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────
def _es_factura_completa(resultado: dict) -> bool:
    return bool(resultado.get("comercio")) and bool(resultado.get("fecha"))

def _parsear_fecha(fecha_raw) -> date | None:
    if not fecha_raw:
        return None
    if isinstance(fecha_raw, date):
        return fecha_raw
    try:
        return date.fromisoformat(str(fecha_raw))
    except ValueError:
        return None

async def _guardar_como_factura(
    db: AsyncSession,
    usuario_id,
    canal: str,
    resultado: dict,
    raw_text: str = None,
) -> Factura:
    factura = Factura(
        usuario_id=usuario_id,
        canal=canal,
        comercio=resultado.get("comercio"),
        fecha_factura=_parsear_fecha(resultado.get("fecha")),
        total=resultado.get("total_factura"),
        raw_text=raw_text,
    )
    db.add(factura)
    await db.flush()
    for categoria, total in resultado["categorias"].items():
        db.add(ResumenCategoria(
            factura_id=factura.id,
            categoria=categoria,
            total=total,
        ))
    await db.commit()
    await db.refresh(factura)
    return factura

async def _guardar_como_gastos_manuales(
    db: AsyncSession,
    usuario_id,
    canal: str,
    resultado: dict,
) -> list[GastoManual]:
    gastos = []
    fecha = _parsear_fecha(resultado.get("fecha"))
    for categoria, total in resultado["categorias"].items():
        gasto = GastoManual(
            usuario_id=usuario_id,
            canal=canal,
            descripcion=resultado.get("comercio") or "Cargado desde foto",
            monto=total,
            categoria=categoria,
            fecha=fecha,
        )
        db.add(gasto)
        gastos.append(gasto)
    await db.commit()
    for g in gastos:
        await db.refresh(g)
    return gastos

def _formatear_preview(resultado: dict) -> dict:
    return {
        "comercio":      resultado.get("comercio"),
        "fecha":         resultado.get("fecha"),
        "total_factura": resultado.get("total_factura"),
        "categorias":    resultado.get("categorias", {}),
        "tipo":          "factura" if _es_factura_completa(resultado) else "gastos_manuales",
    }

# ─────────────────────────────────────────────────────────────────────────────
# TEXTO — preview y confirmar
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/texto/preview")
async def preview_factura_texto(
    payload: FacturaTextoCreate,
    usuario: Usuario = Depends(get_usuario_actual),
):
    texto = payload.texto.strip()
    if not texto:
        raise HTTPException(status_code=422, detail="El texto no puede estar vacío.")
    try:
        resultado = categorizar_factura_texto(texto)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not resultado.get("categorias"):
        raise HTTPException(status_code=422, detail="No se pudo extraer información de la factura.")
    return {**_formatear_preview(resultado), "raw_text": texto}

@router.post("/texto/confirmar", response_model=FacturaOut, status_code=status.HTTP_201_CREATED)
async def confirmar_factura_texto(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    if not payload.get("categorias"):
        raise HTTPException(status_code=422, detail="Datos de factura inválidos.")
    if _es_factura_completa(payload):
        return await _guardar_como_factura(
            db, usuario.id, "web", payload, raw_text=payload.get("raw_text")
        )
    else:
        await _guardar_como_gastos_manuales(db, usuario.id, "web", payload)
        raise HTTPException(
            status_code=200,
            detail="Guardado como gastos manuales por falta de comercio o fecha.",
        )

# ─────────────────────────────────────────────────────────────────────────────
# FOTO — preview y confirmar
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/foto/preview")
async def preview_factura_foto(
    file: UploadFile = File(...),
    usuario: Usuario = Depends(get_usuario_actual),
):
    if file.content_type not in MIME_PERMITIDOS:
        raise HTTPException(
            status_code=422,
            detail=f"Formato no soportado. Usa: {', '.join(MIME_PERMITIDOS)}",
        )
    contenido = await file.read()
    if len(contenido) > 10 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="La imagen no puede superar 10MB.")
    imagen_b64 = base64.b64encode(contenido).decode()
    try:
        resultado = categorizar_factura_imagen(imagen_b64)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not resultado.get("categorias"):
        raise HTTPException(
            status_code=422,
            detail="No se pudo extraer información útil de la imagen. Intenta con una foto más clara.",
        )
    return {**_formatear_preview(resultado), "imagen_b64": imagen_b64}

@router.post("/foto/confirmar", status_code=status.HTTP_201_CREATED)
async def confirmar_factura_foto(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    if not payload.get("categorias"):
        raise HTTPException(status_code=422, detail="Datos de factura inválidos.")
    if _es_factura_completa(payload):
        factura = await _guardar_como_factura(db, usuario.id, "telegram", payload)
        return {
            "tipo":       "factura",
            "id":         str(factura.id),
            "comercio":   factura.comercio,
            "fecha":      str(factura.fecha_factura),
            "total":      str(factura.total),
            "categorias": payload["categorias"],
        }
    else:
        gastos = await _guardar_como_gastos_manuales(db, usuario.id, "telegram", payload)
        return {
            "tipo":       "gastos_manuales",
            "registros":  len(gastos),
            "categorias": payload["categorias"],
            "nota":       "Guardado como gastos manuales por falta de comercio o fecha.",
        }

@router.get("/", response_model=list[FacturaOut])
async def listar_facturas(
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    result = await db.execute(
        select(Factura)
        .where(Factura.usuario_id == usuario.id)
        .order_by(Factura.creado_en.desc())
    )
    return result.scalars().all()
