#!/usr/bin/env bash
set -euo pipefail

# Qdrant snapshot restore from S3-compatible storage (MinIO on-prem).
# ALPHA / EP-09, Prompt 15 (analytics/ALPHA-PROMPTS.md), ADR-0005.
#
# Flow:
#   1. List runs under s3://bucket/prefix/collection/ (default: latest run)
#   2. Download every <node>.snapshot of the run
#   3. POST /collections/{c}/snapshots/upload?priority=<priority> (multipart)
#      to the node the snapshot was taken from (fallback: first URL)
#   4. Verify: GET /collections/{c} -> points_count / shards_count
#
# A run contains one snapshot per cluster node — ALL of them are uploaded,
# otherwise the restored collection would be missing shards.
#
# priority=snapshot (default) makes snapshot data win over existing data;
# restoring into an existing collection OVERWRITES recovered shards.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log() { echo "[qdrant-restore] $*"; }
err() { echo "[qdrant-restore] ERROR: $*" >&2; }

usage() {
  cat <<'EOF'
Qdrant snapshot restore from S3-compatible storage (MinIO).

Usage: qdrant_restore.sh [options]

Options:
  --list           List backup runs in S3 and exit.
  --run ID         Run id to restore (default: latest available).
  --priority P     Snapshot priority: snapshot | replica | no_sync [snapshot].
  --dry-run        Show the restore plan without changing Qdrant.
  --yes            Skip the confirmation prompt (for non-interactive use).
  -h, --help       Show this help.

Environment (defaults in brackets):
  QDRANT_URLS        Comma-separated node base URLs [http://localhost:6333]
  QDRANT_COLLECTION  Collection name [prompt_attacks]
  S3_ENDPOINT        S3 endpoint URL; empty = real AWS S3 []
  S3_BUCKET          Bucket name [qdrant-backups]
  S3_PREFIX          Key prefix [qdrant/snapshots]

Variables are read from the environment first, then from <repo>/.env.

Exit codes: 0 = success, 1 = failure/aborted.
EOF
}

LIST_ONLY=0
RUN_ID=""
PRIORITY="snapshot"
DRY_RUN=0
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list) LIST_ONLY=1; shift ;;
    --run)
      [[ $# -ge 2 ]] || { err "--run requires a value"; exit 1; }
      RUN_ID="$2"
      shift 2
      ;;
    --priority)
      [[ $# -ge 2 ]] || { err "--priority requires a value"; exit 1; }
      PRIORITY="$2"
      shift 2
      ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
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
case "${PRIORITY}" in
  snapshot|replica|no_sync) ;;
  *) err "--priority must be snapshot|replica|no_sync (got: ${PRIORITY})"; exit 1 ;;
esac
if [[ -n "${RUN_ID}" ]] && ! [[ "${RUN_ID}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  err "invalid --run '${RUN_ID}' (allowed: letters, digits, . _ -)"
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

S3_PREFIX="${S3_PREFIX%/}"
S3_COLLECTION_URI="s3://${S3_BUCKET}/${S3_PREFIX}/${QDRANT_COLLECTION}"

# --- S3 helpers --------------------------------------------------------------

RUN_IDS=()
collect_runs() {
  RUN_IDS=()
  local keys_line key rel run r
  local collected=()
  while IFS= read -r keys_line; do
    [[ -z "${keys_line}" ]] && continue
    read -r _d _t _s key <<< "${keys_line}"
    rel="${key#"${S3_PREFIX}/${QDRANT_COLLECTION}/"}"
    [[ "${rel}" == "${key}" ]] && continue
    run="${rel%%/*}"
    [[ "${run}" == "${rel}" ]] && continue
    collected+=("${run}")
  done < <(s3 ls "${S3_COLLECTION_URI}/" --recursive || true)
  while IFS= read -r r; do
    [[ -n "${r}" ]] || continue
    RUN_IDS+=("${r}")
  done < <(printf '%s\n' "${collected[@]}" | sort -u)
  return 0
}

list_backup_runs() {
  local keys_line key rel run size
  local rows=()
  while IFS= read -r keys_line; do
    [[ -z "${keys_line}" ]] && continue
    read -r _d _t size key <<< "${keys_line}"
    rel="${key#"${S3_PREFIX}/${QDRANT_COLLECTION}/"}"
    [[ "${rel}" == "${key}" ]] && continue
    run="${rel%%/*}"
    [[ "${run}" == "${rel}" ]] && continue
    rows+=("${run} ${size}")
  done < <(s3 ls "${S3_COLLECTION_URI}/" --recursive || true)

  if [[ ${#rows[@]} -eq 0 ]]; then
    echo "(no backups under ${S3_COLLECTION_URI}/)"
    return 0
  fi
  printf '%-20s %8s %14s\n' "RUN" "FILES" "BYTES"
  printf '%s\n' "${rows[@]}" \
    | awk '{ s[$1] += $2; c[$1]++ } END { for (r in s) printf "%-20s %8d %14d\n", r, c[r], s[r] }' \
    | sort -r
}

# --- list mode ---------------------------------------------------------------

if [[ "${LIST_ONLY}" -eq 1 ]]; then
  list_backup_runs
  exit 0
fi

# --- resolve run -------------------------------------------------------------

collect_runs
if [[ ${#RUN_IDS[@]} -eq 0 ]]; then
  err "no backup runs found under ${S3_COLLECTION_URI}/ (run qdrant_backup.sh first)"
  exit 1
fi
if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="${RUN_IDS[${#RUN_IDS[@]}-1]}"
  log "using latest run: ${RUN_ID}"
fi

RUN_KEYS=()
while IFS= read -r keys_line; do
  [[ -z "${keys_line}" ]] && continue
  read -r _d _t _s key <<< "${keys_line}"
  [[ "${key#"${S3_PREFIX}/${QDRANT_COLLECTION}/${RUN_ID}/"}" == "${key}" ]] && continue
  RUN_KEYS+=("${key}")
done < <(s3 ls "${S3_COLLECTION_URI}/${RUN_ID}/" --recursive || true)

if [[ ${#RUN_KEYS[@]} -eq 0 ]]; then
  err "run '${RUN_ID}' not found or empty under ${S3_COLLECTION_URI}/ (see --list)"
  exit 1
fi

target_url_for() {
  local node="$1" url
  for url in "${NODE_URLS[@]}"; do
    if [[ "$(node_id_from_url "${url}")" == "${node}" ]]; then
      printf '%s' "${url}"
      return 0
    fi
  done
  printf '%s' "${NODE_URLS[0]}"
}

# --- plan --------------------------------------------------------------------

log "restore plan:"
log "  run:       ${RUN_ID}"
log "  collection: ${QDRANT_COLLECTION}  priority: ${PRIORITY}"
log "  target:    ${QDRANT_URLS}"
for key in "${RUN_KEYS[@]}"; do
  file="${key##*/}"
  node="${file%.snapshot}"
  log "  ${file} -> $(target_url_for "${node}")"
done

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "dry run — nothing changed"
  exit 0
fi

# --- confirmation ------------------------------------------------------------

if [[ "${ASSUME_YES}" -ne 1 ]]; then
  answer=""
  if ! read -r -p "Restore run ${RUN_ID} into '${QDRANT_COLLECTION}'? Recovered shards are OVERWRITTEN. [y/N] " answer; then
    err "no TTY for confirmation; re-run with --yes"
    exit 1
  fi
  case "${answer}" in
    y|Y|yes|YES) ;;
    *) err "aborted"; exit 1 ;;
  esac
fi

# --- download ----------------------------------------------------------------

TMP_DIR="$(mktemp -d)"
SUCCESS=0
cleanup_tmp() {
  if [[ "${SUCCESS}" -eq 1 ]]; then
    rm -rf "${TMP_DIR}"
  elif [[ -n "$(ls -A "${TMP_DIR}" 2>/dev/null || true)" ]]; then
    err "downloaded snapshots kept in ${TMP_DIR}"
  else
    rm -rf "${TMP_DIR}"
  fi
}
trap cleanup_tmp EXIT

LOCAL_FILES=()
for key in "${RUN_KEYS[@]}"; do
  file="${key##*/}"
  log "downloading ${key} ..."
  if ! s3 cp "s3://${S3_BUCKET}/${key}" "${TMP_DIR}/${file}" >/dev/null; then
    err "cannot download s3://${S3_BUCKET}/${key}"
    exit 1
  fi
  LOCAL_FILES+=("${TMP_DIR}/${file}")
done

# --- restore (upload to Qdrant) ----------------------------------------------

for local_file in "${LOCAL_FILES[@]}"; do
  file="${local_file##*/}"
  node="${file%.snapshot}"
  target="$(target_url_for "${node}")"
  log "recovering ${file} on ${target} (priority=${PRIORITY}) ..."
  if ! "${CURL[@]}" -X POST \
      "${target%/}/collections/${QDRANT_COLLECTION}/snapshots/upload?priority=${PRIORITY}" \
      -F "snapshot=@${local_file}"; then
    err "recovery of ${file} failed on ${target}"
    exit 1
  fi
done

# --- verify ------------------------------------------------------------------

primary="${NODE_URLS[0]}"
if info="$("${CURL[@]}" "${primary%/}/collections/${QDRANT_COLLECTION}" 2>/dev/null)"; then
  points="$(jq -r '.result.points_count // "n/a"' <<<"${info}")"
  shards="$(jq -r '.result.shards_count // "n/a"' <<<"${info}")"
  status="$(jq -r '.result.status // "n/a"' <<<"${info}")"
  log "collection '${QDRANT_COLLECTION}': status=${status} points=${points} shards=${shards}"
  if [[ "${points}" == "0" ]]; then
    log "WARN: collection reports 0 points — check Qdrant logs and rerun with --list/--run"
  fi
else
  log "WARN: cannot read collection info from ${primary} for verification"
fi

SUCCESS=1
log "done: run ${RUN_ID} restored into '${QDRANT_COLLECTION}'"
