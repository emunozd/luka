from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.models import CargueEmail, Factura
from app.schemas.schemas import CargueEmailCreate, CargueEmailOut, FacturaOut

router = APIRouter(prefix="/email", tags=["email"])


@router.post("/importar", response_model=CargueEmailOut, status_code=status.HTTP_201_CREATED)
async def iniciar_cargue_email(
    payload: CargueEmailCreate,
    db: AsyncSession = Depends(get_db),
):
    cargue = CargueEmail(
        fecha_inicio=payload.fecha_inicio,
        fecha_fin=payload.fecha_fin,
        total_facturas=0,
    )
    db.add(cargue)
    try:
        await db.commit()
        await db.refresh(cargue)
    except IntegrityError as e:
        await db.rollback()
        if "cargues_email_no_solapamiento" in str(e.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe un cargue para ese rango de fechas o un rango solapado.",
            )
        raise HTTPException(status_code=500, detail="Error al registrar el cargue.")
    return cargue


@router.get("/cargues", response_model=list[CargueEmailOut])
async def listar_cargues(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CargueEmail).order_by(CargueEmail.creado_en.desc())
    )
    return result.scalars().all()


@router.get("/cargues/{cargue_id}", response_model=CargueEmailOut)
async def obtener_cargue(cargue_id: UUID, db: AsyncSession = Depends(get_db)):
    cargue = await db.get(CargueEmail, cargue_id)
    if not cargue:
        raise HTTPException(status_code=404, detail="Cargue no encontrado")
    return cargue


@router.get("/cargues/{cargue_id}/facturas", response_model=list[FacturaOut])
async def facturas_por_cargue(cargue_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Factura)
        .where(Factura.cargue_email_id == cargue_id)
        .options(selectinload(Factura.resumen_categorias))
        .order_by(Factura.fecha_factura.desc())
    )
    return result.scalars().all()
