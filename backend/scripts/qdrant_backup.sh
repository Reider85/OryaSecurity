#!/usr/bin/env bash
set -euo pipefail

# Qdrant snapshot backup -> S3-compatible storage (MinIO on-prem).
# ALPHA / EP-09, Prompt 15 (analytics/ALPHA-PROMPTS.md), ADR-0005.
#
# Flow:
#   1. POST /collections/{collection}/snapshots            (per node)
#   2. GET  /collections/{c}/snapshots/{name}              (download tar)
#   3. aws s3 cp -> s3://bucket/prefix/collection/<run>/<node>.snapshot
#   4. DELETE remote snapshot                              (free node disk)
#   5. retention: keep the last SNAPSHOT_KEEP runs in S3
#
# A collection-level snapshot is node-local: in a cluster every node must be
# snapshotted (QDRANT_URLS lists all nodes).
#
# Cron (every 6 hours):
#   0 */6 * * * cd /path/to/OryaSecurity && bash backend/scripts/qdrant_backup.sh >> /var/log/qdrant-backup.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log() { echo "[qdrant-backup] $*"; }
err() { echo "[qdrant-backup] ERROR: $*" >&2; }

usage() {
  cat <<'EOF'
Qdrant snapshot backup to S3-compatible storage (MinIO).

Usage: qdrant_backup.sh [options]

Options:
  --run-id ID    Backup run id (default: current UTC timestamp, e.g. 20261024T120000Z).
  --no-cleanup   Skip retention cleanup in S3 (keep everything).
  -h, --help     Show this help.

Environment (defaults in brackets):
  QDRANT_URLS        Comma-separated node base URLs [http://localhost:6333]
  QDRANT_COLLECTION  Collection name [prompt_attacks]
  S3_ENDPOINT        S3 endpoint URL; empty = real AWS S3 []
  S3_BUCKET          Bucket name [qdrant-backups]
  S3_PREFIX          Key prefix [qdrant/snapshots]
  SNAPSHOT_KEEP      Backup runs to keep in S3 [7]

Variables are read from the environment first, then from <repo>/.env.

Exit codes: 0 = success, 1 = failure.
EOF
}

RUN_ID=""
CLEANUP=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-id)
      [[ $# -ge 2 ]] || { err "--run-id requires a value"; exit 1; }
      RUN_ID="$2"
      shift 2
      ;;
    --no-cleanup) CLEANUP=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "unknown option: $1"; usage >&2; exit 1 ;;
  esac
done

# --- environment -------------------------------------------------------------

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

load_dotenv() {
  local file="$1" line key value
  [[ -f "${file}" ]] || return 0
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "${line}" || "${line}" == \#* ]] && continue
    [[ "${line}" == export\ * ]] && line="${line#export }"
    [[ "${line}" == *"="* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ -n "${!key+x}" ]] && continue
    if [[ ${#value} -ge 2 && ( "${value:0:1}" == '"' && "${value: -1}" == '"' || "${value:0:1}" == "'" && "${value: -1}" == "'" ) ]]; then
      value="${value:1:${#value}-2}"
    fi
    printf -v "${key}" '%s' "${value}"
    export "${key}"
  done < "${file}"
}

load_dotenv "${ROOT_DIR}/.env"

QDRANT_URLS="${QDRANT_URLS:-${QDRANT_URL:-http://localhost:6333}}"
QDRANT_COLLECTION="${QDRANT_COLLECTION:-prompt_attacks}"
S3_ENDPOINT="${S3_ENDPOINT:-}"
S3_BUCKET="${S3_BUCKET:-qdrant-backups}"
S3_PREFIX="${S3_PREFIX:-qdrant/snapshots}"
SNAPSHOT_KEEP="${SNAPSHOT_KEEP:-7}"

if ! command -v curl >/dev/null 2>&1; then
  err "curl not found."
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  err "jq not found. Install jq (apt-get/brew/winget install jq)."
  exit 1
fi
if ! command -v aws >/dev/null 2>&1; then
  err "aws CLI not found. Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
  exit 1
fi
if [[ -z "${S3_BUCKET}" ]]; then
  err "S3_BUCKET must not be empty."
  exit 1
fi
if ! [[ "${SNAPSHOT_KEEP}" =~ ^[0-9]+$ ]] || (( SNAPSHOT_KEEP < 1 )); then
  err "SNAPSHOT_KEEP must be a positive integer (got: ${SNAPSHOT_KEEP})"
  exit 1
fi
if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
fi
if ! [[ "${RUN_ID}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  err "invalid --run-id '${RUN_ID}' (allowed: letters, digits, . _ -)"
  exit 1
fi

s3() {
  if [[ -n "${S3_ENDPOINT}" ]]; then
    aws --endpoint-url "${S3_ENDPOINT}" "$@"
  else
    aws "$@"
  fi
}

node_id_from_url() {
  local u="${1#*://}"
  u="${u%%/*}"
  printf '%s' "${u//[^A-Za-z0-9.-]/-}"
}

CURL=(curl -fsS --connect-timeout 10 --max-time 600)

# --- resolve node URLs -------------------------------------------------------

IFS=',' read -r -a RAW_URLS <<< "${QDRANT_URLS}"
NODE_URLS=()
for raw in "${RAW_URLS[@]}"; do
  raw="$(trim "${raw}")"
  [[ -n "${raw}" ]] || continue
  NODE_URLS+=("${raw}")
done
if [[ ${#NODE_URLS[@]} -eq 0 ]]; then
  err "QDRANT_URLS is empty."
  exit 1
fi

TMP_DIR="$(mktemp -d)"
SUCCESS=0
cleanup_tmp() {
  if [[ "${SUCCESS}" -eq 1 ]]; then
    rm -rf "${TMP_DIR}"
  elif [[ -n "$(ls -A "${TMP_DIR}" 2>/dev/null || true)" ]]; then
    err "local files kept in ${TMP_DIR}"
  else
    rm -rf "${TMP_DIR}"
  fi
}
trap cleanup_tmp EXIT

S3_PREFIX="${S3_PREFIX%/}"
S3_RUN_URI="s3://${S3_BUCKET}/${S3_PREFIX}/${QDRANT_COLLECTION}/${RUN_ID}"

# --- phase 1: create + download snapshots (per node) -------------------------

LOCAL_FILES=()
LOCAL_NODES=()

log "run ${RUN_ID}: collection '${QDRANT_COLLECTION}' from ${#NODE_URLS[@]} node(s)"

for url in "${NODE_URLS[@]}"; do
  node="$(node_id_from_url "${url}")"
  resp=""
  if ! resp="$("${CURL[@]}" -X POST "${url%/}/collections/${QDRANT_COLLECTION}/snapshots")"; then
    err "cannot create snapshot on ${url} — Qdrant unreachable or collection '${QDRANT_COLLECTION}' missing"
    exit 1
  fi
  if ! snap_name="$(jq -re '.result.snapshot_name' <<<"${resp}" 2>/dev/null)"; then
    err "unexpected create-snapshot response from ${url}: ${resp}"
    exit 1
  fi

  local_file="${TMP_DIR}/${node}.snapshot"
  log "downloading ${snap_name} from ${node} ..."
  if ! "${CURL[@]}" --output "${local_file}" "${url%/}/collections/${QDRANT_COLLECTION}/snapshots/${snap_name}"; then
    err "download of ${snap_name} failed on ${node}"
    "${CURL[@]}" -X DELETE "${url%/}/collections/${QDRANT_COLLECTION}/snapshots/${snap_name}" >/dev/null 2>&1 || true
    exit 1
  fi

  # The snapshot is only a copy — the collection data stays on the node.
  if ! "${CURL[@]}" -X DELETE "${url%/}/collections/${QDRANT_COLLECTION}/snapshots/${snap_name}" >/dev/null; then
    log "WARN: cannot delete remote snapshot ${snap_name} on ${node}; delete it manually to free disk"
  fi

  log "snapshot from ${node}: $(du -h "${local_file}" | cut -f1)"
  LOCAL_FILES+=("${local_file}")
  LOCAL_NODES+=("${node}")
done

# --- phase 2: upload to S3 ---------------------------------------------------

log "uploading ${#LOCAL_FILES[@]} snapshot(s) to ${S3_RUN_URI}/"
for i in "${!LOCAL_FILES[@]}"; do
  if ! s3 cp "${LOCAL_FILES[$i]}" "${S3_RUN_URI}/${LOCAL_NODES[$i]}.snapshot" >/dev/null; then
    err "S3 upload failed for node ${LOCAL_NODES[$i]}; local copy kept: ${LOCAL_FILES[$i]}"
    exit 1
  fi
  log "uploaded ${LOCAL_NODES[$i]}.snapshot"
done

# --- phase 3: retention (keep last SNAPSHOT_KEEP runs) -----------------------

cleanup_old_runs() {
  local keys_line key rel run r
  local collected=()
  local all_runs=()

  while IFS= read -r keys_line; do
    [[ -z "${keys_line}" ]] && continue
    read -r _d _t _s key <<< "${keys_line}"
    rel="${key#"${S3_PREFIX}/${QDRANT_COLLECTION}/"}"
    [[ "${rel}" == "${key}" ]] && continue
    run="${rel%%/*}"
    [[ "${run}" == "${rel}" ]] && continue
    collected+=("${run}")
  done < <(s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/${QDRANT_COLLECTION}/" --recursive || true)

  while IFS= read -r r; do
    [[ -n "${r}" ]] || continue
    all_runs+=("${r}")
  done < <(printf '%s\n' "${collected[@]}" | sort -u)

  local total=${#all_runs[@]}
  if (( total <= SNAPSHOT_KEEP )); then
    log "retention: ${total} run(s) in S3, keep ${SNAPSHOT_KEEP} — nothing to delete"
    return 0
  fi

  local excess=$(( total - SNAPSHOT_KEEP ))
  local i
  for (( i = 0; i < excess; i++ )); do
    log "retention: removing old run ${all_runs[$i]}"
    s3 rm "s3://${S3_BUCKET}/${S3_PREFIX}/${QDRANT_COLLECTION}/${all_runs[$i]}/" --recursive >/dev/null
  done
}

if [[ "${CLEANUP}" -eq 1 ]]; then
  cleanup_old_runs
else
  log "retention skipped (--no-cleanup)"
fi

# --- summary -----------------------------------------------------------------

SUCCESS=1
log "done: ${S3_RUN_URI}/ (nodes: ${LOCAL_NODES[*]})"
