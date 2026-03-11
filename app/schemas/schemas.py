from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr


# ─────────────────────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────────────────────
class SolicitarCodigoRequest(BaseModel):
    email: EmailStr
    nombre: Optional[str] = None  # solo requerido en registro


class VerificarCodigoRequest(BaseModel):
    email: EmailStr
    codigo: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UsuarioOut(BaseModel):
    id: UUID
    nombre: str
    email: str
    activo: bool
    creado_en: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# GASTOS MANUALES
# ─────────────────────────────────────────────────────────────────────────────
class GastoManualCreate(BaseModel):
    canal: str
    descripcion: str
    fecha: Optional[date] = None


class GastoManualOut(BaseModel):
    id: UUID
    canal: str
    descripcion: str
    monto: Decimal
    categoria: str
    fecha: date
    creado_en: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# FACTURAS
# ─────────────────────────────────────────────────────────────────────────────
class FacturaTextoCreate(BaseModel):
    texto: str


class FacturaOut(BaseModel):
    id: UUID
    canal: str
    comercio: Optional[str]
    fecha_factura: Optional[date]
    total: Optional[Decimal]
    moneda: str
    creado_en: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# CARGUES EMAIL
# ─────────────────────────────────────────────────────────────────────────────
class CargueEmailCreate(BaseModel):
    fecha_inicio: date
    fecha_fin: date


class CargueEmailOut(BaseModel):
    id: UUID
    fecha_inicio: date
    fecha_fin: date
    total_facturas: int
    creado_en: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# RESUMEN
# ─────────────────────────────────────────────────────────────────────────────
class ResumenCategoriaOut(BaseModel):
    categoria: str
    total: Decimal

    model_config = {"from_attributes": True}


class GastoMensualOut(BaseModel):
    mes: str  # formato YYYY-MM
    categoria: str
    total: Decimal

    model_config = {"from_attributes": True}
