#!/usr/bin/env bash
# Bring up EduQGen **dev2.1** only:
#   - web → :8001  (ngrok public demo)  DB=qgen_db
#   - Django runserver in tmux session "eduqgen21"
#
# Usage:
#   bash scripts/start_dev21.sh
#   bash scripts/start_dev21.sh --status
#   bash scripts/start_dev21.sh --stop
#
set -euo pipefail

ROOT="/home/pankaj/code/bharatgen-ibm-yojaka-llm-board"
QGEN="${ROOT}/qgen_project"
TMUX_SESSION="eduqgen21"
HOST_IP="${HOST_IP:-10.129.6.47}"
NGROK_URL="https://slighted-dispersal-flap.ngrok-free.dev"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }
}

status() {
  echo "=== tmux ==="
  tmux ls 2>/dev/null | grep -E "^${TMUX_SESSION}:" || echo "(no ${TMUX_SESSION} session)"
  echo
  echo "=== containers ==="
  docker ps --filter name=qgen_project --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
  echo
  echo "=== HTTP ==="
  curl -s -o /dev/null -w "2.1  http://${HOST_IP}:8001  %{http_code}\n" --max-time 5 "http://127.0.0.1:8001/" || echo "2.1  DOWN"
  curl -s -o /dev/null -w "ngrok  ${NGROK_URL}  %{http_code}\n" \
    --max-time 8 -H 'ngrok-skip-browser-warning: 1' \
    "${NGROK_URL}/" || echo "ngrok DOWN/unreachable"
  echo
  echo "=== Ollama ==="
  curl -s -o /dev/null -w "OLLAMA %{http_code}  (${OLLAMA_BASE_URL:-http://10.129.7.47:11434})\n" \
    --max-time 5 "${OLLAMA_BASE_URL:-http://10.129.7.47:11434}/api/tags" || echo "Ollama DOWN"
}

stop_demo() {
  log "Stopping tmux session ${TMUX_SESSION}"
  tmux kill-session -t "${TMUX_SESSION}" 2>/dev/null || true
  log "Stopping 2.1 web/celery/beat (keep db/redis)"
  (cd "${QGEN}" && docker compose -f docker-compose.dev.yml -f docker-compose.tmux.yml stop web celery_worker celery_beat) || true
  log "Stopped."
}

ensure_paths() {
  [[ -d "${QGEN}" ]] || { echo "Missing path: ${QGEN}" >&2; exit 1; }
  [[ -f "${QGEN}/.env" ]] || { echo "Missing ${QGEN}/.env" >&2; exit 1; }
  if [[ ! -f "${QGEN}/docker-compose.tmux.yml" ]]; then
    cat > "${QGEN}/docker-compose.tmux.yml" <<'EOF'
services:
  web:
    command: sleep infinity
EOF
  fi
}

start_infra() {
  log "Starting 2.1 stack (db/redis/web/celery) — web idle for tmux runserver"
  cd "${QGEN}"
  if grep -q '^DB_NAME=' .env; then
    sed -i 's/^DB_NAME=.*/DB_NAME=qgen_db/' .env
  else
    echo 'DB_NAME=qgen_db' >> .env
  fi
  docker compose -f docker-compose.dev.yml -f docker-compose.tmux.yml up -d db redis
  docker compose -f docker-compose.dev.yml -f docker-compose.tmux.yml up -d --no-deps web celery_worker celery_beat
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

  tmux new-session -d -s "${TMUX_SESSION}" -n 'web' \
    "docker exec -i qgen_project-web-1 python -u manage.py runserver 0.0.0.0:8000 2>&1 | tee /tmp/eduqgen-21.log"

  tmux new-window -t "${TMUX_SESSION}" -n 'status' \
    "watch -n 5 'curl -s -o /dev/null -w \"2.1 :8001 %{http_code}\\n\" http://127.0.0.1:8001/; echo; docker ps --filter name=qgen_project --format \"table {{.Names}}\\t{{.Status}}\"'"

  log "Waiting for HTTP on :8001…"
  for _ in $(seq 1 30); do
    c1=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8001/ || true)
    if [[ "${c1}" =~ ^(200|302)$ ]]; then
      break
    fi
    sleep 1
  done
}

print_urls() {
  cat <<EOF

============================================================
  EduQGen dev2.1 is up (tmux: ${TMUX_SESSION})
============================================================
  Attach:   tmux attach -t ${TMUX_SESSION}
  Detach:   Ctrl+B then D
  Windows:  0=web  1=status

  URLs
    Local:   http://${HOST_IP}:8001/
    Ngrok:   ${NGROK_URL}
             (needs: tmux session ngrok → http 8001)

  Log:     /tmp/eduqgen-21.log

  Start ngrok if needed:
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
  start_infra
  wait_db
  start_tmux
  print_urls
  status
}

case "${1:-}" in
  --status|-s) status ;;
  --stop)      stop_demo ;;
  --help|-h)   sed -n '2,12p' "$0" ;;
  *)           main_start ;;
esac
