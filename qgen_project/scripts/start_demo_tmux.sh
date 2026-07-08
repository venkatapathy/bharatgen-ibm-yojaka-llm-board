#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="${1:-qgen-demo}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if tmux has-session -t "${SESSION_NAME}" 2>/dev/null; then
  echo "tmux session '${SESSION_NAME}' already exists"
  exit 0
fi

tmux new-session -d -s "${SESSION_NAME}" -c "${PROJECT_DIR}"
tmux rename-window -t "${SESSION_NAME}:0" web
tmux send-keys -t "${SESSION_NAME}:web" "docker compose -f docker-compose.dev.yml up --build" C-m

tmux new-window -t "${SESSION_NAME}" -n shell -c "${PROJECT_DIR}"
tmux send-keys -t "${SESSION_NAME}:shell" "echo 'Attach with: tmux attach -t ${SESSION_NAME}'" C-m

echo "Started tmux session '${SESSION_NAME}'"
