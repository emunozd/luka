# LUKA — Lucas Under Kontrol AI

Personal finance assistant with local AI inference. Captures expenses from natural language text, receipt photos, and Telegram messages, automatically categorizes them, and stores them in PostgreSQL for personal spending reports.

---

## Stack

| Layer | Technology |
|---|---|
| AI Inference | MLX + Qwen3.5-35B-A3B-4bit (via AIBase) |
| API | FastAPI + SQLAlchemy async |
| Database | PostgreSQL 16 |
| Bot | python-telegram-bot 21 |
| Containers | Docker Desktop (Mac) |
| Reverse proxy | Caddy (separate repo `kingsrow-caddy`) |
| Registry | `quay.io/kingsrow/luka-*` |

---

## Project structure

```
luka/
├── app/
│   ├── core/
│   │   ├── config.py        ← configuration via pydantic-settings
│   │   ├── database.py      ← async SQLAlchemy session
│   │   └── deps.py          ← FastAPI dependencies (JWT auth)
│   ├── models/
│   │   └── models.py        ← ORM models (User, ManualExpense, Invoice, etc.)
│   ├── routers/
│   │   ├── auth.py          ← registration, login, Telegram linking
│   │   ├── gastos.py        ← manual expense CRUD
│   │   ├── facturas.py      ← invoice upload and confirmation
│   │   ├── reportes.py      ← monthly and annual reports
│   │   └── email.py         ← email invoice import
│   ├── schemas/
│   │   └── schemas.py       ← Pydantic input/output models
│   └── services/
│       └── ai_client.py     ← HTTP client toward AIBase
├── bot/
│   ├── __init__.py
│   ├── main.py              ← Telegram bot, handlers and callbacks
│   └── agent.py             ← conversational agent with intent classifier
├── db/
│   └── init/                ← SQL initialization scripts
├── docker-compose.yml
├── Dockerfile               ← luka-api image
├── Dockerfile.bot           ← luka-bot image
├── requirements.txt
├── requirements.bot.txt
└── env.example
```

---

## Requirements

- Docker Desktop
- AIBase running on the local network
- Telegram bot token (via [@BotFather](https://t.me/BotFather))
- Brevo account (for email verification code delivery)

---

## Configuration

```bash
cp env.example .env
```

Edit `.env` with real values:

```env
# Database
POSTGRES_DB=luka
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_PORT=5433

# API
API_PORT=8000

# MLX endpoint — AIBase base URL including /luka prefix
MLX_SERVER_URL=http://<aibase-ip>:8181/luka

# Security
JWT_SECRET=

# Brevo (email delivery)
BREVO_API_KEY=
BREVO_FROM_EMAIL=
BREVO_FROM_NAME=LUKA

# Telegram
TELEGRAM_TOKEN=
```

---

## Starting up

```bash
docker compose up -d
```

The three services start in order:
1. `luka-postgres` — database (healthcheck before proceeding)
2. `luka-api` — FastAPI on the port set in `API_PORT`
3. `luka-bot` — Telegram bot

### Rebuild after changes

```bash
# Bot only
docker compose build bot && docker compose up -d bot

# API only
docker compose build api && docker compose up -d api

# Everything
docker compose build && docker compose up -d
```

### View logs

```bash
docker compose logs -f bot
docker compose logs -f api
```

---

## API reference

### Authentication

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/solicitar-codigo` | Sends verification code to email |
| `POST` | `/auth/verificar-codigo` | Verifies code and returns JWT |
| `POST` | `/auth/vincular-telegram` | Links Telegram ID to authenticated user |
| `DELETE` | `/auth/desvincular-telegram` | Unlinks Telegram (history preserved) |
| `GET` | `/auth/token-telegram/{telegram_id}` | Retrieves JWT by Telegram ID |

### Manual expenses

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/gastos/manual` | Records expense(s) from a text description |
| `POST` | `/gastos/manual/confirmar` | Saves pre-categorized items (no re-inference) |
| `GET` | `/gastos/manual` | Lists all user expenses |
| `DELETE` | `/gastos/manual/{id}` | Deletes an expense |

### Invoices

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/facturas/foto/preview` | Analyzes image and returns preview without saving |
| `POST` | `/facturas/foto/confirmar` | Saves the confirmed invoice |
| `POST` | `/facturas/texto/preview` | Analyzes text and returns preview without saving |
| `POST` | `/facturas/texto/confirmar` | Saves the confirmed invoice |
| `GET` | `/facturas/` | Lists user invoices |
| `DELETE` | `/facturas/{id}` | Deletes an invoice |

### Reports

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/reportes/mensual` | Current month report, or specific month (`?mes=YYYY-MM`) |
| `GET` | `/reportes/anual` | Current year report, or specific year (`?anio=YYYY`) |
| `GET` | `/reportes/categorias/resumen` | Category summary over a date range |

---

## Telegram bot

### Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and command list |
| `/vincular` | Link LUKA account to current Telegram |
| `/desvincular` | Unlink Telegram (history preserved) |
| `/gasto <description>` | Record expense(s) in natural language |
| `/reporte` | Current month summary |
| `/reporte YYYY-MM` | Specific month summary |
| `/ultimos` | Last 5 records |
| `/borrar <number>` | Delete a record from the previous list |

### Natural language (conversational agent)

In addition to commands, the bot accepts free text:

- **"Paid 15k for the bus"** → detects expense, categorizes, shows preview, asks for confirmation
- **"How much did I spend this month"** → shows current month report
- **"February report"** → shows the specified month report
- **"Last expenses"** → lists the 5 most recent records with delete option
- **"Delete the second one"** → identifies the record and asks for confirmation
- Also accepts **receipt photos** → analyzes the image, shows preview, asks for confirmation

### Agent flow

```
free text
    ↓
lightweight classifier (EXPENSE / REPORT / RECENT / DELETE / OTHER)
    ↓
direct Python action (no model re-invocation for logic decisions)
    ↓
model generates final natural language response
```

---

## Expense categories

Categories are fixed and defined in the database model:

`HOGAR` · `CANASTA` · `MEDICAMENTOS` · `OCIO` · `ANTOJO` · `TRANSPORTE` · `TECNOLOGÍA` · `ROPA` · `EDUCACIÓN` · `MASCOTAS`

---

## Database

The schema is initialized automatically on first startup from the scripts in `db/init/`.

PostgreSQL data is persisted in a local volume outside Docker to prevent data loss when recreating containers. **Do not use SMB/NFS volumes for pgdata** — causes fsync errors on macOS.

---

## Timezone

All containers must run with the correct timezone to avoid incorrect dates on records. In `docker-compose.yml`:

```yaml
environment:
  TZ: America/Bogota        # for api and bot services
  PGTZ: America/Bogota      # additional for postgres
```

---

## Multi-user

The system supports multiple users. Each user registers with their email and can link their Telegram account. All expenses and invoices are tied to `usuario_id`, so unlinking Telegram does not delete the history.

---

## Architecture notes

- AI inference runs **outside Docker** on the host (Apple Silicon native via MLX), not inside a container. Containers consume it over HTTP.
- The bot and API communicate over an internal Docker network (`luka_net`), not exposed externally.
- The reverse proxy (Caddy) is the only external entry point and lives in a separate repo.
- The AI abstraction in `ai_client.py` means switching models only requires updating `MLX_SERVER_URL` in `.env`.
