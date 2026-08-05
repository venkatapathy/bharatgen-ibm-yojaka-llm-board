#!/usr/bin/env bash
# Bring up EduQGen dual demo:
#   - dev2.1  → :8001  (ngrok public demo)  DB=qgen_db
#   - dev2.3  → :8002  (server/work)        DB=qgen_db_dev23
#   - Django runservers managed in tmux session "eduqgen"
#
# Usage:
#   bash scripts/start_dual_demo.sh
#   bash scripts/start_dual_demo.sh --status
#   bash scripts/start_dual_demo.sh --stop
#
set -euo pipefail

ROOT_21="/home/pankaj/code/bharatgen-ibm-yojaka-llm-board"
ROOT_23="/home/pankaj/code/bharatgen-ibm-yojaka-llm-board-dev23"
QGEN_21="${ROOT_21}/qgen_project"
QGEN_23="${ROOT_23}/qgen_project"
TMUX_SESSION="eduqgen"
HOST_IP="${HOST_IP:-10.129.6.47}"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }
}

status() {
  echo "=== tmux ==="
  tmux ls 2>/dev/null | grep -E "^${TMUX_SESSION}:" || echo "(no ${TMUX_SESSION} session)"
  echo
  echo "=== containers ==="
  docker ps --filter name=qgen --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
  echo
  echo "=== HTTP ==="
  curl -s -o /dev/null -w "2.1  http://${HOST_IP}:8001  %{http_code}\n" --max-time 5 "http://127.0.0.1:8001/" || echo "2.1  DOWN"
  curl -s -o /dev/null -w "2.3  http://${HOST_IP}:8002  %{http_code}\n" --max-time 5 "http://127.0.0.1:8002/" || echo "2.3  DOWN"
  curl -s -o /dev/null -w "ngrok  https://slighted-dispersal-flap.ngrok-free.dev  %{http_code}\n" \
    --max-time 8 -H 'ngrok-skip-browser-warning: 1' \
    "https://slighted-dispersal-flap.ngrok-free.dev/" || echo "ngrok DOWN/unreachable"
  echo
  echo "=== Ollama ==="
  curl -s -o /dev/null -w "OLLAMA_BASE_URL %{http_code}  (${OLLAMA_BASE_URL:-http://10.129.7.47:11434})\n" \
    --max-time 5 "${OLLAMA_BASE_URL:-http://10.129.7.47:11434}/api/tags" || echo "Ollama DOWN"
}

stop_demo() {
  log "Stopping tmux session ${TMUX_SESSION}"
  tmux kill-session -t "${TMUX_SESSION}" 2>/dev/null || true
  log "Stopping 2.3 web/celery (project qgen23)"
  (cd "${QGEN_23}" && docker compose -p qgen23 -f docker-compose.dev23.yml -f docker-compose.tmux.yml down) || true
  log "Stopping 2.1 web/celery/beat (keep db/redis)"
  (cd "${QGEN_21}" && docker compose -f docker-compose.dev.yml -f docker-compose.tmux.yml stop web celery_worker celery_beat) || true
  log "Stopped. DB/redis left running."
}

ensure_paths() {
  [[ -d "${QGEN_21}" ]] || { echo "Missing 2.1 path: ${QGEN_21}" >&2; exit 1; }
  [[ -d "${QGEN_23}" ]] || { echo "Missing 2.3 worktree: ${QGEN_23}" >&2; exit 1; }
  [[ -f "${QGEN_21}/.env" ]] || { echo "Missing ${QGEN_21}/.env" >&2; exit 1; }
  [[ -f "${QGEN_23}/.env" ]] || { echo "Missing ${QGEN_23}/.env" >&2; exit 1; }
  [[ -f "${QGEN_21}/docker-compose.tmux.yml" ]] || {
    cat > "${QGEN_21}/docker-compose.tmux.yml" <<'EOF'
services:
  web:
    command: sleep infinity
EOF
  }
  [[ -f "${QGEN_23}/docker-compose.tmux.yml" ]] || {
    cat > "${QGEN_23}/docker-compose.tmux.yml" <<'EOF'
services:
  web:
    command: sleep infinity
EOF
  }
}

start_infra_21() {
  log "Starting 2.1 stack (db/redis/web/celery) — web stays idle for tmux runserver"
  cd "${QGEN_21}"
  # Ensure DB name for public demo
  if grep -q '^DB_NAME=' .env; then
    sed -i 's/^DB_NAME=.*/DB_NAME=qgen_db/' .env
  else
    echo 'DB_NAME=qgen_db' >> .env
  fi
  docker compose -f docker-compose.dev.yml -f docker-compose.tmux.yml up -d db redis
  docker compose -f docker-compose.dev.yml -f docker-compose.tmux.yml up -d --no-deps web celery_worker celery_beat
}

start_infra_23() {
  log "Starting 2.3 slim stack (web/celery on :8002, shares db/redis/media)"
  cd "${QGEN_23}"
  if grep -q '^DB_NAME=' .env; then
    sed -i 's/^DB_NAME=.*/DB_NAME=qgen_db_dev23/' .env
  else
    echo 'DB_NAME=qgen_db_dev23' >> .env
  fi
  # Redis DB 1 so celery queues don't clash with 2.1
  if grep -q '^REDIS_URL=' .env; then
    sed -i 's|^REDIS_URL=.*|REDIS_URL=redis://redis:6379/1|' .env
  else
    echo 'REDIS_URL=redis://redis:6379/1' >> .env
  fi
  docker compose -p qgen23 -f docker-compose.dev23.yml -f docker-compose.tmux.yml up -d
}

wait_db() {
  log "Waiting for Postgres healthy…"
  for _ in $(seq 1 40); do
    if docker exec qgen_project-db-1 pg_isready -U postgres >/dev/null 2>&1; then
      log "Postgres ready"
      return 0
    fi
    sleep 1
  done
  echo "Postgres did not become ready" >&2
  exit 1
}

start_tmux() {
  log "Starting tmux session ${TMUX_SESSION}"
  tmux kill-session -t "${TMUX_SESSION}" 2>/dev/null || true

  # Window 0: dev2.1 runserver inside qgen_project-web-1 → host :8001
  tmux new-session -d -s "${TMUX_SESSION}" -n 'dev21' \
    "docker exec -i qgen_project-web-1 python -u manage.py runserver 0.0.0.0:8000 2>&1 | tee /tmp/eduqgen-21.log"

  # Window 1: dev2.3 runserver inside qgen23-web-1 → host :8002
  tmux new-window -t "${TMUX_SESSION}" -n 'dev23' \
    "docker exec -i qgen23-web-1 python -u manage.py runserver 0.0.0.0:8000 2>&1 | tee /tmp/eduqgen-23.log"

  # Window 2: live status
  tmux new-window -t "${TMUX_SESSION}" -n 'status' \
    "watch -n 5 'curl -s -o /dev/null -w \"2.1 :8001 %{http_code}\\n\" http://127.0.0.1:8001/; curl -s -o /dev/null -w \"2.3 :8002 %{http_code}\\n\" http://127.0.0.1:8002/; echo; docker ps --filter name=qgen --format \"table {{.Names}}\\t{{.Status}}\"'"

  log "Waiting for HTTP…"
  for _ in $(seq 1 30); do
    c1=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8001/ || true)
    c2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8002/ || true)
    if [[ "${c1}" =~ ^(200|302)$ && "${c2}" =~ ^(200|302)$ ]]; then
      break
    fi
    sleep 1
  done
}

print_urls() {
  cat <<EOF

============================================================
  EduQGen dual demo is up (tmux: ${TMUX_SESSION})
============================================================
  Attach:   tmux attach -t ${TMUX_SESSION}
  Detach:   Ctrl+B then D
  Windows:  0=dev21  1=dev23  2=status

  URLs
    2.1 (public / ngrok backend):  http://${HOST_IP}:8001/
    2.3 (work):                    http://${HOST_IP}:8002/
    Ngrok (must already be up):    https://slighted-dispersal-flap.ngrok-free.dev
                                   (tmux session: ngrok → http 8001)

  Logs
    /tmp/eduqgen-21.log
    /tmp/eduqgen-23.log

  Ngrok (if not running):
    tmux new-session -d -s ngrok \\
      'ngrok http 8001 --domain=slighted-dispersal-flap.ngrok-free.dev'
============================================================
EOF
}

main_start() {
  need docker
  need tmux
  need curl
  ensure_paths
  start_infra_21
  wait_db
  start_infra_23
  start_tmux
  print_urls
  status
}

case "${1:-}" in
  --status|-s) status ;;
  --stop)      stop_demo ;;
  --help|-h)
    sed -n '2,12p' "$0"
    ;;
  *)           main_start ;;
esac
