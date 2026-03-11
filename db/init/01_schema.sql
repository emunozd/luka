-- ─────────────────────────────────────────────────────────────
-- LUKA – Schema inicial
-- PostgreSQL 16
-- ─────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "btree_gist";

-- ─────────────────────────────────────────────────────────────
-- ENUMS
-- ─────────────────────────────────────────────────────────────
CREATE TYPE canal_ingreso AS ENUM (
    'email',
    'telegram',
    'web'
);

CREATE TYPE categoria_gasto AS ENUM (
    'HOGAR',
    'CANASTA',
    'MEDICAMENTOS',
    'OCIO',
    'ANTOJO',
    'TRANSPORTE',
    'TECNOLOGÍA',
    'ROPA',
    'EDUCACIÓN',
    'MASCOTAS'
);

-- ─────────────────────────────────────────────────────────────
-- USUARIOS
-- ─────────────────────────────────────────────────────────────
CREATE TABLE usuarios (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre      TEXT        NOT NULL,
    email       TEXT        NOT NULL UNIQUE,
    activo      BOOLEAN     NOT NULL DEFAULT true,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT usuarios_email_formato CHECK (email ~* '^[^@]+@[^@]+\.[^@]+$')
);

-- ─────────────────────────────────────────────────────────────
-- CODIGOS DE VERIFICACION
-- ─────────────────────────────────────────────────────────────
CREATE TABLE codigos_verificacion (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT        NOT NULL,
    codigo      CHAR(6)     NOT NULL,
    expira_en   TIMESTAMPTZ NOT NULL,
    usado       BOOLEAN     NOT NULL DEFAULT false,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────
-- TELEGRAM
-- ─────────────────────────────────────────────────────────────
CREATE TABLE telegram_cuentas (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id          UUID        NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    telegram_id         BIGINT      NOT NULL UNIQUE,
    username_telegram   TEXT,
    vinculado_en        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────
-- CARGUES EMAIL
-- ─────────────────────────────────────────────────────────────
CREATE TABLE cargues_email (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      UUID        NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    fecha_inicio    DATE        NOT NULL,
    fecha_fin       DATE        NOT NULL,
    total_facturas  INTEGER     NOT NULL DEFAULT 0,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cargues_email_rango_valido CHECK (fecha_fin >= fecha_inicio),
    CONSTRAINT cargues_email_no_solapamiento
        EXCLUDE USING gist (
            usuario_id WITH =,
            daterange(fecha_inicio, fecha_fin, '[]') WITH &&
        )
);

-- ─────────────────────────────────────────────────────────────
-- FACTURAS
-- ─────────────────────────────────────────────────────────────
CREATE TABLE facturas (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      UUID          NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    canal           canal_ingreso NOT NULL,
    comercio        TEXT,
    fecha_factura   DATE,
    total           NUMERIC(12, 2),
    moneda          CHAR(3)       NOT NULL DEFAULT 'COP',
    raw_text        TEXT,
    cargue_email_id UUID          REFERENCES cargues_email(id) ON DELETE SET NULL,
    creado_en       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────
-- RESUMEN CATEGORIAS
-- ─────────────────────────────────────────────────────────────
CREATE TABLE resumen_categorias (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    factura_id  UUID            NOT NULL REFERENCES facturas(id) ON DELETE CASCADE,
    categoria   categoria_gasto NOT NULL,
    total       NUMERIC(12, 2)  NOT NULL DEFAULT 0,
    CONSTRAINT resumen_categorias_uq UNIQUE (factura_id, categoria),
    CONSTRAINT resumen_categorias_total_positivo CHECK (total >= 0)
);

-- ─────────────────────────────────────────────────────────────
-- GASTOS MANUALES
-- ─────────────────────────────────────────────────────────────
CREATE TABLE gastos_manuales (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id  UUID            NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    canal       canal_ingreso   NOT NULL,
    descripcion TEXT            NOT NULL,
    monto       NUMERIC(12, 2)  NOT NULL,
    categoria   categoria_gasto NOT NULL,
    fecha       DATE            NOT NULL DEFAULT CURRENT_DATE,
    creado_en   TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT gastos_manuales_monto_positivo CHECK (monto > 0)
);

-- ─────────────────────────────────────────────────────────────
-- ÍNDICES
-- ─────────────────────────────────────────────────────────────
CREATE INDEX idx_usuarios_email             ON usuarios (email);
CREATE INDEX idx_codigos_email              ON codigos_verificacion (email);
CREATE INDEX idx_codigos_expira             ON codigos_verificacion (expira_en);
CREATE INDEX idx_telegram_usuario           ON telegram_cuentas (usuario_id);
CREATE INDEX idx_telegram_id               ON telegram_cuentas (telegram_id);
CREATE INDEX idx_cargues_usuario            ON cargues_email (usuario_id);
CREATE INDEX idx_facturas_usuario           ON facturas (usuario_id);
CREATE INDEX idx_facturas_fecha             ON facturas (fecha_factura);
CREATE INDEX idx_facturas_canal             ON facturas (canal);
CREATE INDEX idx_resumen_categoria          ON resumen_categorias (categoria);
CREATE INDEX idx_gastos_usuario             ON gastos_manuales (usuario_id);
CREATE INDEX idx_gastos_fecha               ON gastos_manuales (fecha);
CREATE INDEX idx_gastos_categoria           ON gastos_manuales (categoria);

-- ─────────────────────────────────────────────────────────────
-- VISTA
-- ─────────────────────────────────────────────────────────────
CREATE VIEW gasto_mensual_por_categoria AS
    SELECT
        f.usuario_id,
        date_trunc('month', f.fecha_factura)::date AS mes,
        rc.categoria,
        SUM(rc.total) AS total
    FROM resumen_categorias rc
    JOIN facturas f ON f.id = rc.factura_id
    WHERE f.fecha_factura IS NOT NULL
    GROUP BY 1, 2, 3
    UNION ALL
    SELECT
        usuario_id,
        date_trunc('month', fecha)::date AS mes,
        categoria,
        SUM(monto) AS total
    FROM gastos_manuales
    GROUP BY 1, 2, 3;
