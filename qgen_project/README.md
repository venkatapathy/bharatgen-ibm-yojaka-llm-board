# QGen — Django Question Generation Platform

A Django monolith for AI-powered exam question generation with RAG retrieval, PYQ n-shot prompting, and role-based access control.

> **New clone / local laptop?** Start here → **[STARTUP.md](./STARTUP.md)**

## Table of Contents

- [STARTUP.md — clone & run locally](./STARTUP.md)
- [Prerequisites](#prerequisites)
- [Project Structure](#project-structure)
- [Setup — Docker (recommended)](#setup--docker-recommended)
- [Setup — Local](#setup--local)
- [Environment Variables](#environment-variables)
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
cp .env.example .env
```

Edit `.env` — at minimum set these:

```env
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=*

DB_NAME=qgen_db
DB_USER=postgres
DB_PASSWORD=postgres

OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

> **Note:** `DB_HOST` and `REDIS_URL` are overridden by docker-compose — leave them as-is in `.env`.

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

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Django secret key | — |
| `DEBUG` | `True` / `False` | `True` |
| `ALLOWED_HOSTS` | Comma-separated hostnames/IPs | `localhost,127.0.0.1` |
| `DB_NAME` | PostgreSQL database name | `qgen_db` |
| `DB_USER` | PostgreSQL user | `postgres` |
| `DB_PASSWORD` | PostgreSQL password | `postgres` |
| `DB_HOST` | PostgreSQL host | `localhost` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `REDIS_URL` | Celery broker URL | `redis://localhost:6379/0` |
| `OPENAI_API_KEY` | OpenAI key (used by LiteLLM) | — |
| `ANTHROPIC_API_KEY` | Anthropic key (used by LiteLLM) | — |
| `AWS_ACCESS_KEY_ID` | S3 access key (optional) | — |
| `AWS_SECRET_ACCESS_KEY` | S3 secret key (optional) | — |
| `AWS_STORAGE_BUCKET_NAME` | S3 bucket name (optional) | — |

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
