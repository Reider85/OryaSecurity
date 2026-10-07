#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

MINIMAL=0
DESTROY=0

usage() {
  cat <<'EOF'
OryaSecurity - stop the project stack

Usage: stop.sh [options]

Options:
  --minimal    Stop only core services (postgres, redis, scanner).
               Default: stop the full stack (UI, Prometheus, Grafana).
  --destroy    Also remove data volumes (postgres, redis, grafana data).
  -h, --help   Show this help.
EOF
}

for arg in "$@"; do
  case "${arg}" in
    --minimal) MINIMAL=1 ;;
    --destroy) DESTROY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: ${arg}" >&2; usage >&2; exit 1 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found. Install Docker Desktop / Docker Engine." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose plugin not available (need Docker Compose v2)." >&2
  exit 1
fi

COMPOSE=(docker compose)
if [[ ${MINIMAL} -eq 0 ]]; then
  COMPOSE+=(--profile ui --profile observability)
fi

DOWN_ARGS=(down)
if [[ ${DESTROY} -eq 1 ]]; then
  DOWN_ARGS+=(-v)
fi

echo "[stop] Stopping services (docker compose ${DOWN_ARGS[*]})..."
if ! "${COMPOSE[@]}" "${DOWN_ARGS[@]}"; then
  echo "ERROR: docker compose down failed." >&2
  exit 1
fi

if [[ ${DESTROY} -eq 1 ]]; then
  echo "[stop] Services stopped, data volumes removed."
else
  echo "[stop] Services stopped, data volumes preserved (use --destroy to remove)."
fi
exit 0
