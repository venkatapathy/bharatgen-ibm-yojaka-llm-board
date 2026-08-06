# EduQGen — Local startup guide

For someone who **clones this repo** and wants to run **EduQGen (Django)** on their own machine.

Use the **`dev2.3-IGNOV`** branch (or the branch your team points you to).

---

## What you need

| Tool | Notes |
|------|--------|
| Docker Desktop / Docker Engine | 24+ |
| Docker Compose | v2 (`docker compose …`) |
| Git | |
| (Optional) Ollama | Local LLMs for generation/OCR |
| (Optional) API keys | Groq / OpenAI / etc. if not using Ollama |

**You do not need** the lab’s dual-stack (8001/8002) setup. That is server-only.

---

## 1. Clone

```bash
git clone <REPO_URL>
cd bharatgen-ibm-yojaka-llm-board/qgen_project
git checkout dev2.3-IGNOV
```

---

## 2. Create `.env`

```bash
cp .env.example .env
```

Edit `.env` if you want. Defaults are fine for a first local run:

- `SECRET_KEY` — change it
- `DEBUG=True`
- `DB_*` — used inside Docker; compose overrides `DB_HOST` to `db`
- `OLLAMA_BASE_URL` — your machine’s Ollama, e.g. `http://host.docker.internal:11434` (Mac/Windows) or your LAN IP on Linux
- Leave `REDIS_URL` alone; compose sets it

---

## 3. Start everything (recommended)

Self-contained stack: **Postgres + Redis + web + Celery worker + Celery beat**.

```bash
cd bharatgen-ibm-yojaka-llm-board/qgen_project

docker compose -f docker-compose.dev.yml -p eduqgen up -d --build
```

Wait ~30–60s for migrations (entrypoint runs them).

Open:

**http://localhost:8001**

| Service | Host port |
|---------|-----------|
| Django web | **8001** |
| Postgres | **5434** |
| Redis | **6382** |

---

## 4. Create an admin user

```bash
docker compose -f docker-compose.dev.yml -p eduqgen exec web \
  python manage.py createsuperuser
```

Then log in at http://localhost:8001/accounts/login/

---

## 5. Useful day-to-day commands

```bash
cd bharatgen-ibm-yojaka-llm-board/qgen_project

# Status
docker compose -f docker-compose.dev.yml -p eduqgen ps

# Logs
docker compose -f docker-compose.dev.yml -p eduqgen logs -f --tail=100
docker compose -f docker-compose.dev.yml -p eduqgen logs -f web
docker compose -f docker-compose.dev.yml -p eduqgen logs -f celery_worker

# Restart after code edits (bind-mounted; often auto-reloads)
docker compose -f docker-compose.dev.yml -p eduqgen restart web celery_worker celery_beat

# Rebuild only if Dockerfile / requirements.txt changed
docker compose -f docker-compose.dev.yml -p eduqgen up -d --build

# Django shell / migrate
docker compose -f docker-compose.dev.yml -p eduqgen exec web python manage.py shell
docker compose -f docker-compose.dev.yml -p eduqgen exec web python manage.py migrate

# Stop (keeps data volumes)
docker compose -f docker-compose.dev.yml -p eduqgen stop

# Stop and remove containers (volumes kept unless you add -v)
docker compose -f docker-compose.dev.yml -p eduqgen down
```

---

## 6. LLM / OCR (optional but needed to generate)

Generation and PDF OCR call external services:

1. **Ollama** — set `OLLAMA_BASE_URL` in `.env` to a reachable host (not `localhost` from inside Docker unless you use `host.docker.internal`).
2. Pull a model Ollama-side, e.g. whatever your team uses for generation.
3. In the UI: **Control → Technical settings** — pick prompt + generation model, save.
4. Upload a **PDF Context**, wait until status is **ready**, then **Generate → New Run**.

Without a working LLM endpoint, the UI still runs; generation tasks will fail in Celery logs.

---

## 7. Smoke check

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/accounts/login/
# expect 200
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Port 8001 / 5434 / 6382 in use | Change the left side of the port mapping in `docker-compose.dev.yml` |
| `DB connection refused` | Wait for healthy `db`; check `docker compose … logs db` |
| Generation stuck / errors | `logs -f celery_worker`; check `OLLAMA_BASE_URL` from inside the container |
| PDF stuck processing | Same Celery logs; OCR URL/model env vars |
| Permission / Docker | Ensure your user can run Docker without sudo, or use `sudo` |

---

## Project layout (what matters)

```
qgen_project/
├── docker/Dockerfile          # Image for web + celery
├── docker-compose.dev.yml     # Local all-in-one (use this)
├── apps/                      # Django apps
├── templates/ static/         # UI
├── .env.example               # Copy to .env
└── STARTUP.md                 # This file
```

---

## Note for the lab server (ignore on your laptop)

On the shared lab host there is also a **8002 / `docker-compose.dev23.yml`** stack that reuses that server’s Postgres/Redis. **Do not use that compose for a personal laptop clone** — use `docker-compose.dev.yml` as above.
