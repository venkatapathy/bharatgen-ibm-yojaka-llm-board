# QGen — Django Question Generation Platform

A Django monolith for AI-powered exam question generation with RAG retrieval, PYQ n-shot prompting, and role-based access control.

> **New clone / local laptop?** Start here → **[STARTUP.md](./STARTUP.md)**  
> **Config:** copy **`.env.example` → `.env`**. Real `.env` is gitignored and never pushed.

## Table of Contents

- [STARTUP.md — clone & run locally](./STARTUP.md)
- [Prerequisites](#prerequisites)
- [Project Structure](#project-structure)
- [Setup — Docker (recommended)](#setup--docker-recommended)
- [Setup — Local](#setup--local)
- [Environment Variables (`.env`)](#environment-variables-env)
- [Roles](#roles)
- [API Reference](#api-reference)
- [Key Models](#key-models)
- [Extending the Project](#extending-the-project)

---

## Prerequisites

| Tool | Version |
|------|---------|
| Docker | 24+ |
| Docker Compose | v2+ |
| Python | 3.11+ (local setup only) |
| PostgreSQL | 15+ with `pgvector` (local setup only) |
| Redis | 7+ (local setup only) |

---

## Project Structure

```
qgen_project/
├── docker/
│   ├── Dockerfile          # Single image for web, celery_worker, celery_beat
│   ├── entrypoint.sh       # Waits for postgres, runs makemigrations + migrate
│   └── init-db.sql         # Enables pgvector extension on first DB init
├── docker-compose.dev.yml  # Dev: web drops to bash, source mounted for live reload
├── docker-compose.prod.yml # Prod: gunicorn auto-starts, no source mount
├── qgen/                   # Django project (settings, urls, wsgi, celery)
│   └── settings/
│       ├── base.py
│       ├── development.py
│       └── production.py
├── apps/
│   ├── core/               # User model, roles, ModelConfig, provisioning quotas
│   ├── pdf_module/         # PDF upload → chunking → pgvector embedding
│   ├── pyq_module/         # PYQ paper upload → LLM extraction → Question table
│   ├── prompt_module/      # Prompt template CRUD with version history
│   ├── question_generation/# Batch run engine (Celery) + results
│   └── api/                # DRF viewsets for all modules
├── static/                 # CSS + JS
├── templates/              # Django HTML templates
├── media/                  # User uploads (git-ignored)
└── requirements.txt
```

---

## Setup — Docker (recommended)

### 1. Clone and configure environment

```bash
git clone <repo-url>
cd qgen_project
git checkout dev2.3-IGNOV   # or the branch your team uses
cp .env.example .env
```

**About `.env`:**

| File | In git? | Purpose |
|------|---------|---------|
| `.env.example` | Yes | Template — same keys as the team uses; **no real secrets** |
| `.env` | **No** (gitignored) | Your private config — create with `cp .env.example .env` |

Edit `.env` and set at least:

- `SECRET_KEY` — change the default
- `GROQ_API_KEY` — if you use Groq (optional)
- `OLLAMA_BASE_URL` — your Ollama host (required for local LLM generation)

Docker Compose overrides `DB_HOST` → `db` and `REDIS_URL` → the compose Redis service. Keep the other `DB_*` values unless you know you need different ones.

See **[STARTUP.md](./STARTUP.md)** for the full local checklist.

### 2. Development

Build and start all services (postgres, redis, celery_worker, celery_beat, web):

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

The `web` container starts in bash. Exec into it and start the dev server:

```bash
docker compose -f docker-compose.dev.yml exec web bash
# inside the container:
python manage.py runserver 0.0.0.0:8000
```

Visit **http://\<host-ip\>:8000**

To create a superuser:

```bash
python manage.py createsuperuser
```

#### Port mappings (dev)

| Service | Host port | Notes |
|---------|-----------|-------|
| Django | 8000 | Dev server |
| PostgreSQL | 5433 | Mapped to 5433 to avoid conflict with a local postgres |
| Redis | 6379 | |

> If you have a local Redis on 6379 as well, change `"6379:6379"` to `"6380:6379"` in `docker-compose.dev.yml`.

### 3. Production

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Gunicorn starts automatically with 4 workers. Static files are collected at startup. No source code is mounted — the image is self-contained.

---

## Setup — Local

### 1. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. PostgreSQL setup

```sql
-- In psql:
CREATE DATABASE qgen_db;
\c qgen_db
CREATE EXTENSION IF NOT EXISTS vector;
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env — set DB_HOST=localhost, REDIS_URL=redis://localhost:6379/0
```

### 4. Run migrations

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

### 5. Start services

```bash
# Terminal 1 — Django dev server
python manage.py runserver

# Terminal 2 — Celery worker
celery -A qgen worker -l info

# Terminal 3 — Celery beat (optional)
celery -A qgen beat -l info
```

---

## Environment Variables (`.env`)

Secrets and runtime config live in **`.env`**, which is **not committed**.

1. Copy the template: `cp .env.example .env`
2. Fill in your values (API keys, Ollama URL, etc.)
3. Never commit `.env` — only update `.env.example` when you add a **new key name** (with an empty/placeholder value)

| Variable | Description | Notes |
|----------|-------------|--------|
| `SECRET_KEY` | Django secret key | Change for any shared/deployed host |
| `DEBUG` | `True` / `False` | `True` for local |
| `ALLOWED_HOSTS` | Comma-separated hosts | Add tunnel domains if needed |
| `DB_NAME` | PostgreSQL database | e.g. `qgen_db` or `qgen_db_dev23` |
| `DB_USER` / `DB_PASSWORD` | Postgres credentials | Defaults match compose |
| `DB_HOST` / `DB_PORT` | Postgres host/port | Compose sets `DB_HOST=db` |
| `REDIS_URL` | Celery broker | Compose overrides this |
| `GROQ_API_KEY` | Groq API key | Optional; leave blank if unused |
| `OLLAMA_BASE_URL` | Ollama HTTP base | Must be reachable **from inside Docker** |
| `UNLIMITED_OCR_URL` | OCR service URL | Optional |
| `UNLIMITED_OCR_MODEL` | OCR model id | Optional |
| `PDF_FORCE_UNLIMITED_OCR` | `0` / `1` | Force vision OCR path |

Full field-by-field notes: **[STARTUP.md § Create `.env`](./STARTUP.md#2-create-env-required)**.

---

## Roles

| Role | Scope | Permissions |
|------|-------|-------------|
| `superuser` | Global | Everything, including org management |
| `orguser` | Organisation | Configure presets, manage users, view all runs |
| `user` | Own runs only | Upload PDFs/PYQs, create generation runs |

---

## API Reference

All endpoints are under `/api/`. Browse the interactive API at `/api/` (session auth in dev).

| Resource | Endpoint |
|----------|----------|
| Organisations | `GET/POST /api/organizations/` |
| Users | `GET/POST /api/users/` |
| PDF Contexts | `GET /api/pdf/contexts/` |
| PYQ Modules | `GET /api/pyq/modules/` |
| Questions | `GET /api/questions/` |
| Prompt Templates | `GET /api/prompts/` |
| Batch Runs | `GET/POST /api/generate/runs/` |

Authentication: session (browser) or token (`Authorization: Token <token>`).

---

## Key Models

- **Organization** — groups users and settings
- **User** — extends `AbstractUser`, has `role` and `organization`
- **ModelConfig** — stores LLM/embedding/reranker config; API key stored as an env-var name, not the value
- **PDFContext** — one or more PDFs chunked and embedded into pgvector
- **PDFChunk** — individual pgvector chunk
- **PYQModule** — exam paper; LLM extracts Questions from it
- **Question** — shared table for PYQ-extracted and AI-generated questions
- **PromptTemplate** + **PromptVersion** — editable prompts with full version history
- **BatchRun** + **BatchRunItem** — orchestrates the generation pipeline via Celery
- **UserProvisioningQuota**, **StorageQuota**, **ExecutionQuota** — per-user resource limits

---

## Extending the Project

### Adding a new chunking strategy

1. Add a function in `apps/pdf_module/chunkers.py`
2. Register it in `STRATEGY_MAP`
3. Add the choice to `ChunkingStrategy` in `apps/pdf_module/models.py`
4. Run `python manage.py makemigrations && python manage.py migrate`

### Adding a new LLM provider

Models are configured via `ModelConfig` in the admin panel. Any provider supported by LiteLLM works — add the API key as an environment variable and reference the variable name in the config.
