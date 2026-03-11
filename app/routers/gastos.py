from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.core.deps import get_usuario_actual
from app.models.models import GastoManual, Usuario
from app.schemas.schemas import GastoManualCreate, GastoManualOut
from app.services.ai_client import categorizar_gasto_manual

router = APIRouter(prefix="/gastos", tags=["gastos"])


@router.post("/manual", response_model=list[GastoManualOut], status_code=status.HTTP_201_CREATED)
async def crear_gasto_manual(
    payload: GastoManualCreate,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    try:
        items = categorizar_gasto_manual(payload.descripcion)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    if not items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No se pudieron extraer gastos de la descripción.",
        )

    gastos_creados = []
    for item in items:
        monto = item.get("monto")
        if monto is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No se pudo extraer el monto para '{item.get('descripcion', payload.descripcion)}'. Incluye el valor.",
            )
        gasto = GastoManual(
            usuario_id=usuario.id,
            canal=payload.canal,
            descripcion=item.get("descripcion") or payload.descripcion,
            monto=monto,
            categoria=item["categoria"],
            fecha=payload.fecha,
        )
        db.add(gasto)
        gastos_creados.append(gasto)

    await db.commit()
    for g in gastos_creados:
        await db.refresh(g)

    return gastos_creados


@router.get("/manual", response_model=list[GastoManualOut])
async def listar_gastos_manuales(
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    result = await db.execute(
        select(GastoManual)
        .where(GastoManual.usuario_id == usuario.id)
        .order_by(GastoManual.fecha.desc())
    )
    return result.scalars().all()


@router.delete("/manual/{gasto_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_gasto_manual(
    gasto_id: str,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    result = await db.execute(
        select(GastoManual).where(
            GastoManual.id == gasto_id,
            GastoManual.usuario_id == usuario.id,
        )
    )
    gasto = result.scalar_one_or_none()
    if not gasto:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gasto no encontrado.")
    await db.delete(gasto)
    await db.commit()
