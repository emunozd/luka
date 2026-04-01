from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_usuario_actual
from app.models.models import Usuario
from app.schemas.schemas import GastoMensualOut, ReporteRangoOut

router = APIRouter(prefix="/reportes", tags=["reportes"])


def _formato_mes(d: date) -> str:
    return d.strftime("%Y-%m")


@router.get("/mensual", response_model=list[GastoMensualOut])
async def reporte_mensual(
    mes: Optional[str] = Query(None, description="Mes en formato YYYY-MM. Si no se indica, devuelve el mes actual."),
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    if mes:
        try:
            fecha_mes = date.fromisoformat(f"{mes}-01")
        except ValueError:
            raise HTTPException(status_code=422, detail="Formato de mes inválido. Usa YYYY-MM.")
    else:
        hoy = date.today()
        fecha_mes = date(hoy.year, hoy.month, 1)

    resultado = await db.execute(
        text("""
            SELECT mes, categoria, total
            FROM gasto_mensual_por_categoria
            WHERE usuario_id = :usuario_id
              AND mes = :mes
            ORDER BY total DESC
        """),
        {"usuario_id": str(usuario.id), "mes": fecha_mes},
    )

    rows = resultado.fetchall()
    return [
        GastoMensualOut(mes=_formato_mes(row.mes), categoria=row.categoria, total=Decimal(row.total))
        for row in rows
    ]


@router.get("/anual", response_model=list[GastoMensualOut])
async def reporte_anual(
    anio: Optional[int] = Query(None, description="Año en formato YYYY. Si no se indica, devuelve el año actual."),
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    if not anio:
        anio = date.today().year

    resultado = await db.execute(
        text("""
            SELECT mes, categoria, total
            FROM gasto_mensual_por_categoria
            WHERE usuario_id = :usuario_id
              AND EXTRACT(YEAR FROM mes) = :anio
            ORDER BY mes ASC, total DESC
        """),
        {"usuario_id": str(usuario.id), "anio": anio},
    )

    rows = resultado.fetchall()
    return [
        GastoMensualOut(mes=_formato_mes(row.mes), categoria=row.categoria, total=Decimal(row.total))
        for row in rows
    ]


@router.get("/categorias/resumen", response_model=list[GastoMensualOut])
async def resumen_por_categoria(
    desde: Optional[date] = Query(None, description="Fecha inicio YYYY-MM-DD"),
    hasta: Optional[date] = Query(None, description="Fecha fin YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    hoy = date.today()
    if not desde:
        desde = date(hoy.year, hoy.month, 1)
    if not hasta:
        hasta = hoy

    resultado = await db.execute(
        text("""
            SELECT mes, categoria, SUM(total) as total
            FROM gasto_mensual_por_categoria
            WHERE usuario_id = :usuario_id
              AND mes >= date_trunc('month', :desde::date)
              AND mes <= date_trunc('month', :hasta::date)
            GROUP BY mes, categoria
            ORDER BY total DESC
        """),
        {"usuario_id": str(usuario.id), "desde": desde, "hasta": hasta},
    )

    rows = resultado.fetchall()
    return [
        GastoMensualOut(mes=_formato_mes(row.mes), categoria=row.categoria, total=Decimal(row.total))
        for row in rows
    ]


@router.get("/rango", response_model=list[ReporteRangoOut])
async def reporte_por_rango(
    desde: date = Query(..., description="Fecha inicio YYYY-MM-DD"),
    hasta: date = Query(..., description="Fecha fin YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """
    Reporte de gastos agrupado por categoría para un rango exacto de fechas.
    Consulta directamente las tablas base (no la vista mensual), por lo que
    soporta cualquier granularidad: día, semana, N días, etc.
    """
    if hasta < desde:
        raise HTTPException(status_code=422, detail="'hasta' no puede ser anterior a 'desde'.")

    resultado = await db.execute(
        text("""
            SELECT categoria, SUM(total) AS total
            FROM (
                SELECT
                    rc.categoria,
                    rc.total
                FROM resumen_categorias rc
                JOIN facturas f ON f.id = rc.factura_id
                WHERE f.usuario_id    = :usuario_id
                  AND f.fecha_factura >= :desde
                  AND f.fecha_factura <= :hasta

                UNION ALL

                SELECT
                    categoria,
                    monto AS total
                FROM gastos_manuales
                WHERE usuario_id = :usuario_id
                  AND fecha      >= :desde
                  AND fecha      <= :hasta
            ) combinado
            GROUP BY categoria
            ORDER BY total DESC
        """),
        {"usuario_id": str(usuario.id), "desde": desde, "hasta": hasta},
    )

    rows = resultado.fetchall()
    return [
        ReporteRangoOut(
            desde=desde,
            hasta=hasta,
            categoria=row.categoria,
            total=Decimal(row.total),
        )
        for row in rows
    ]