import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date,
    DateTime, Enum, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = "usuarios"

    id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre    = Column(Text, nullable=False)
    email     = Column(Text, nullable=False, unique=True)
    activo    = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    telegram_cuenta = relationship("TelegramCuenta", back_populates="usuario", uselist=False)
    gastos_manuales = relationship("GastoManual", back_populates="usuario")
    facturas        = relationship("Factura", back_populates="usuario")
    cargues_email   = relationship("CargueEmail", back_populates="usuario")


class CodigoVerificacion(Base):
    __tablename__ = "codigos_verificacion"

    id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email     = Column(Text, nullable=False)
    codigo    = Column(String(6), nullable=False)
    expira_en = Column(DateTime(timezone=True), nullable=False)
    usado     = Column(Boolean, nullable=False, default=False)
    creado_en = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class TelegramCuenta(Base):
    __tablename__ = "telegram_cuentas"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id        = Column(UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    telegram_id       = Column(BigInteger, nullable=False, unique=True)
    username_telegram = Column(Text)
    vinculado_en      = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    usuario = relationship("Usuario", back_populates="telegram_cuenta")


class CargueEmail(Base):
    __tablename__ = "cargues_email"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id     = Column(UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    fecha_inicio   = Column(Date, nullable=False)
    fecha_fin      = Column(Date, nullable=False)
    total_facturas = Column(Integer, nullable=False, default=0)
    creado_en      = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    usuario  = relationship("Usuario", back_populates="cargues_email")
    facturas = relationship("Factura", back_populates="cargue_email")


class Factura(Base):
    __tablename__ = "facturas"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id      = Column(UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    canal           = Column(Enum("email", "telegram", "web", name="canal_ingreso"), nullable=False)
    comercio        = Column(Text)
    fecha_factura   = Column(Date)
    total           = Column(Numeric(12, 2))
    moneda          = Column(String(3), nullable=False, default="COP")
    raw_text        = Column(Text)
    cargue_email_id = Column(UUID(as_uuid=True), ForeignKey("cargues_email.id", ondelete="SET NULL"))
    creado_en       = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    usuario      = relationship("Usuario", back_populates="facturas")
    cargue_email = relationship("CargueEmail", back_populates="facturas")
    categorias   = relationship("ResumenCategoria", back_populates="factura", cascade="all, delete-orphan")


class ResumenCategoria(Base):
    __tablename__ = "resumen_categorias"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    factura_id = Column(UUID(as_uuid=True), ForeignKey("facturas.id", ondelete="CASCADE"), nullable=False)
    categoria  = Column(Enum(
        "HOGAR", "CANASTA", "MEDICAMENTOS", "OCIO", "ANTOJO",
        "TRANSPORTE", "TECNOLOGÍA", "ROPA", "EDUCACIÓN", "MASCOTAS",
        name="categoria_gasto"
    ), nullable=False)
    total      = Column(Numeric(12, 2), nullable=False, default=0)

    factura = relationship("Factura", back_populates="categorias")

    __table_args__ = (
        UniqueConstraint("factura_id", "categoria", name="resumen_categorias_uq"),
        CheckConstraint("total >= 0", name="resumen_categorias_total_positivo"),
    )


class GastoManual(Base):
    __tablename__ = "gastos_manuales"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id  = Column(UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    canal       = Column(Enum("email", "telegram", "web", name="canal_ingreso"), nullable=False)
    descripcion = Column(Text, nullable=False)
    monto       = Column(Numeric(12, 2), nullable=False)
    categoria   = Column(Enum(
        "HOGAR", "CANASTA", "MEDICAMENTOS", "OCIO", "ANTOJO",
        "TRANSPORTE", "TECNOLOGÍA", "ROPA", "EDUCACIÓN", "MASCOTAS",
        name="categoria_gasto"
    ), nullable=False)
    fecha       = Column(Date, nullable=False, default=date.today)
    creado_en   = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    usuario = relationship("Usuario", back_populates="gastos_manuales")

    __table_args__ = (
        CheckConstraint("monto > 0", name="gastos_manuales_monto_positivo"),
    )
