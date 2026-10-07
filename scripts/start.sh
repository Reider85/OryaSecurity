#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

MINIMAL=0

usage() {
  cat <<'EOF'
OryaSecurity - start the project stack

Usage: start.sh [options]

Options:
  --minimal    Start only core services (postgres, redis, scanner).
               Default: full stack (UI, Prometheus, Grafana).
  -h, --help   Show this help.
EOF
}

for arg in "$@"; do
  case "${arg}" in
    --minimal) MINIMAL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: ${arg}" >&2; usage >&2; exit 1 ;;
  esac
done

# --- prerequisites -----------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found. Install Docker Desktop / Docker Engine." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose plugin not available (need Docker Compose v2)." >&2
  exit 1
fi

# --- .env --------------------------------------------------------------------
if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  echo "[start] Created .env from .env.example"
fi

COMPOSE=(docker compose)
if [[ ${MINIMAL} -eq 0 ]]; then
  COMPOSE+=(--profile ui --profile observability)
fi

fail_diagnostics() {
  echo ""
  echo "ERROR: $1" >&2
  echo "--- docker compose ps ---" >&2
  "${COMPOSE[@]}" ps >&2 || true
  echo "--- last 50 log lines ---" >&2
  "${COMPOSE[@]}" logs --tail=50 >&2 || true
  exit 1
}

wait_for_url() {
  local url="$1" name="$2" timeout="$3"
  local elapsed=0
  printf 'Waiting for %-11s (%s) ' "${name}" "${url}"
  if ! command -v curl >/dev/null 2>&1; then
    echo "skipped (curl not found)"
    return 0
  fi
  while (( elapsed < timeout )); do
    if curl -fsS --max-time 3 "${url}" >/dev/null 2>&1; then
      echo "OK (${elapsed}s)"
      return 0
    fi
    printf '.'
    sleep 3
    elapsed=$(( elapsed + 3 ))
  done
  echo " TIMEOUT"
  fail_diagnostics "${name} did not become healthy within ${timeout}s"
}

# --- up ----------------------------------------------------------------------
echo "[start] Building and starting services (docker compose up -d --build)..."
if ! "${COMPOSE[@]}" up -d --build; then
  fail_diagnostics "docker compose up failed."
fi

# --- health checks -----------------------------------------------------------
wait_for_url "http://localhost:8000/health" "scanner" 90
if [[ ${MINIMAL} -eq 0 ]]; then
  wait_for_url "http://localhost:3000/" "ui" 120
  wait_for_url "http://localhost:9090/" "prometheus" 60
  wait_for_url "http://localhost:3001/" "grafana" 60
fi

# --- summary -----------------------------------------------------------------
echo ""
echo "[start] Stack is up:"
echo "  Scanner (API): http://localhost:8000  (/health, /docs, /metrics)"
if [[ ${MINIMAL} -eq 0 ]]; then
  echo "  UI:            http://localhost:3000"
  echo "  Prometheus:    http://localhost:9090"
  echo "  Grafana:       http://localhost:3001  (admin/admin)"
fi
exit 0
