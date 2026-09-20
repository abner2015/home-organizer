# Home Organizer — Backend API

Phase 1: FastAPI base project skeleton (no business logic yet).

## Stack

- Python 3.11+
- FastAPI + Uvicorn
- Pydantic v2 + pydantic-settings
- SQLAlchemy 2.x (async) + Alembic
- PostgreSQL (asyncpg + psycopg)
- Redis (redis-py asyncio)
- structlog (JSON in prod, colored in dev)
- pytest + pytest-asyncio + ruff + mypy

## Directory Layout

```
services/api/
├── app/
│   ├── api/                 # FastAPI routes (Phase 2+)
│   ├── core/                # config, logging, exceptions, health
│   ├── domain/              # Domain Pydantic models (Phase 2+)
│   ├── models/              # SQLAlchemy ORM (Phase 2)
│   ├── repositories/        # DB access layer (Phase 2+)
│   ├── schemas/             # API request/response (Phase 2+)
│   ├── services/            # Business logic (Phase 2+)
│   ├── agents/              # AI Agent (Phases 6-9)
│   ├── tools/               # Agent tools (Phases 6-9)
│   ├── ai/                  # AIProvider protocol + impls (Phase 5)
│   ├── verification/        # Verifier (Phase 7)
│   ├── cache/               # Redis client
│   ├── db/                  # SQLAlchemy base + session
│   └── main.py
├── alembic/                 # Migrations (Phase 2)
├── tests/                   # pytest
├── pyproject.toml
├── alembic.ini
├── Dockerfile
├── .env.example
└── README.md
```

Note: the design docs (`docs/ARCHITECTURE.md`) use `apps/api/`. The
implementation uses `services/api/` per the Phase 1 prompt.

## Setup

```bash
# 1. Create venv + install
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Edit .env: at minimum set JWT_SECRET and AI_API_KEY

# 3. Run
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4. Test
curl http://localhost:8000/health
# OpenAPI:  http://localhost:8000/docs
```

## Lint / Type-check / Test

```bash
ruff check .
ruff format --check .
mypy app
pytest -v
```

## Docker

```bash
docker build -t home-organizer-api .
docker run --rm -p 8000:8000 --env-file .env home-organizer-api
```

## What Phase 1 includes

- FastAPI app with `/` and `/health` endpoints
- Settings via env vars (pydantic-settings)
- Structured logging (structlog → JSON in prod, colored in dev)
- Custom exception hierarchy + global handlers
- SQLAlchemy 2.x async engine + session factory (no models yet)
- Redis client wrapper
- `AIProvider` Protocol stub + Vision/Ranking Pydantic schemas
- Alembic config (no migrations yet — Phase 2)
- Dockerfile + .dockerignore
- pytest setup with /health tests

## What Phase 1 deliberately does NOT include

- No business endpoints
- No database tables / migrations
- No AI provider concrete implementations
- No auth endpoints / user management
