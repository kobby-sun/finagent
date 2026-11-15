#!/usr/bin/env bash
# finagent service control for local development.
#
#   scripts/dev/ctl.sh <command>          (or the matching `make` target)
#
# Local (Python venv):  start | stop | restart | status | logs
# Docker:               up | down | down-clean | dstatus | dlogs
# Data:                 seed | load [CASES...] | load-approve [CASES...] | reset
set -euo pipefail
cd "$(dirname "$0")/../.."
PORT="${PORT:-8080}"; HOST="${HOST:-127.0.0.1}"
RUN_DIR=data/run; PID="$RUN_DIR/finagent.pid"; LOG="$RUN_DIR/finagent.log"
API="${API:-http://127.0.0.1:$PORT}"; export API
PY=.venv/bin/python; FA=.venv/bin/finagent

need_venv() { [ -x "$FA" ] || { echo "venv missing - run: make setup"; exit 1; }; }
running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }
wait_healthy() {
  for _ in $(seq 1 60); do curl -fsS "$API/healthz" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "service did not become healthy - see logs"; return 1
}

cmd="${1:-help}"; shift || true
case "$cmd" in
  start)
    need_venv; mkdir -p "$RUN_DIR"
    if running; then echo "already running (pid $(cat "$PID")) at $API"; exit 0; fi
    set -a; [ -f .env ] && . ./.env; set +a
    [ -f data/index/bm25_index.json ] || "$FA" ingest > /dev/null
    nohup "$FA" serve --host "$HOST" --port "$PORT" > "$LOG" 2>&1 & echo $! > "$PID"
    wait_healthy && echo "started (pid $(cat "$PID")) - API $API  - Swagger $API/docs  - logs: make logs" ;;
  stop)
    if running; then
      pid="$(cat "$PID")"; kill "$pid"
      for _ in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done   # wait until the port is free
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
      rm -f "$PID"; echo "stopped"
    else rm -f "$PID"; echo "not running"; fi ;;
  restart) "$0" stop; "$0" start ;;
  status)
    if running; then echo "local: running (pid $(cat "$PID"))"; else echo "local: stopped"; fi
    curl -fsS "$API/healthz" 2>/dev/null && echo || echo "API $API not reachable" ;;
  logs) tail -n 100 -f "$LOG" ;;
  up) exec python3 scripts/dev/docker_up.py ;;                 # whole environment: models + agent (MODELS=auto|host|docker|mock)
  down) docker compose --profile ollama down ;;
  down-clean) docker compose --profile ollama down -v && echo "containers and data volumes removed (incl. pulled models)" ;;
  dstatus) docker compose ps; curl -fsS "$API/healthz" 2>/dev/null && echo || true ;;
  dlogs) docker compose logs -f --tail 100 finagent ;;
  seed)
    need_venv
    "$PY" scripts/generate_mock_data.py
    "$PY" scripts/skills/write_fixtures.py > /dev/null
    "$FA" ingest > /dev/null && echo "mock data, fixtures and index rebuilt (restart the service to pick up changes)" ;;
  load) "$PY" scripts/dev/load_cases.py "$@" ;;
  load-approve) "$PY" scripts/dev/load_cases.py --with-approvals "$@" ;;
  reset)
    "$0" stop >/dev/null 2>&1 || true
    read -r -p "Delete local runtime data in ./data (runs, approvals, audit, index)? [y/N] " a
    [ "$a" = "y" ] && rm -rf data && echo "deleted - run: make start" ;;
  *)
    sed -n '2,10p' "$0" ;;
esac
