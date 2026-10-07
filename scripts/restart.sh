#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
OryaSecurity - restart the project stack (stop + start)

Usage: restart.sh [options]

Options:
  --minimal    Restart only core services (postgres, redis, scanner).
               Default: full stack (UI, Prometheus, Grafana).
  --destroy    On stop phase also remove data volumes.
  -h, --help   Show this help.
EOF
}

STOP_CMD=(bash "${SCRIPT_DIR}/stop.sh")
START_CMD=(bash "${SCRIPT_DIR}/start.sh")

for arg in "$@"; do
  case "${arg}" in
    --minimal) STOP_CMD+=(--minimal); START_CMD+=(--minimal) ;;
    --destroy) STOP_CMD+=(--destroy) ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: ${arg}" >&2; usage >&2; exit 1 ;;
  esac
done

echo "[restart] Stopping..."
"${STOP_CMD[@]}"
echo ""
echo "[restart] Starting..."
"${START_CMD[@]}"
exit 0
