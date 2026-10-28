#!/usr/bin/env bash
set -euo pipefail

# HashiCorp Vault cluster bootstrap (HA, 3 nodes, Raft).
# ALPHA / EP-13, Prompt 31 (analytics/ALPHA-PROMPTS.md), ADR-0007.
#
# Flow (idempotent — safe to re-run):
#   1. wait until every node answers GET /v1/sys/health
#   2. if node1 is uninitialized -> POST /v1/sys/init (Shamir 5/3),
#      save unseal keys + root token to backend/scripts/.vault-secrets/ (gitignored)
#   3. unseal node1 (threshold of 3 keys) -> it bootstraps Raft and becomes leader
#   4. unseal the remaining nodes (raft retry_join wires them in automatically)
#   5. wait for an active leader (GET /v1/sys/leader)
#   6. enable the secrets engine on $VAULT_MOUNT:
#        - "tokenization" when available (Vault Enterprise, ADR-0007)
#        - "transit" with convergent encryption otherwise (open-source fallback)
#      Both are deterministic: same plaintext -> same token.
#   7. create the encode/decrypt key (idempotent)
#
# Dev mode (docker compose --profile dev up vault): the node is initialized and
# unsealed automatically; provide VAULT_TOKEN (default VAULT_DEV_ROOT_TOKEN) and
# the script skips steps 2-5.
#
# Usage:
#   bash backend/scripts/vault_init.sh
#
# Cron/re-run: none — run once per cluster (or after a re-init).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log()  { echo "[vault-init] $*"; }
err()  { echo "[vault-init] ERROR: $*" >&2; }
warn() { echo "[vault-init] WARN: $*"; }

usage() {
  cat <<'EOF'
HashiCorp Vault HA bootstrap: init, unseal, enable tokenization/transit.

Usage: vault_init.sh [options]

Options:
  --skip-engine   Stop after unseal (do not enable the secrets engine).
  --force-engine  Re-create the mount even if it already exists (destructive).
  -h, --help      Show this help.

Environment (defaults in brackets):
  VAULT_ADDRESSES       Comma-separated node base URLs
                        [http://localhost:8200,http://localhost:8210,http://localhost:8220]
  VAULT_TOKEN           Root/app token; empty = take from secrets dir, then dev-token
  VAULT_MOUNT           Secrets engine mount path [tokenization]
  VAULT_KEY_NAME        Key within the engine [default]
  VAULT_SECRETS_DIR     Where unseal keys / root token are written
                        [<repo>/backend/scripts/.vault-secrets]
  VAULT_DEV_ROOT_TOKEN  Fallback token for dev-mode nodes [dev-token]

Variables are read from the environment first, then from <repo>/.env.

Credentials are written to VAULT_SECRETS_DIR (gitignored, mode 600).
NEVER commit that directory.

Exit codes: 0 = success, 1 = failure.
EOF
}

SKIP_ENGINE=0
FORCE_ENGINE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-engine) SKIP_ENGINE=1; shift ;;
    --force-engine) FORCE_ENGINE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "unknown option: $1"; usage >&2; exit 1 ;;
  esac
done

# --- environment -------------------------------------------------------------

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

VAULT_ADDRESSES="${VAULT_ADDRESSES:-http://localhost:8200,http://localhost:8210,http://localhost:8220}"
VAULT_MOUNT="${VAULT_MOUNT:-tokenization}"
VAULT_MOUNT="${VAULT_MOUNT#/}"
VAULT_MOUNT="${VAULT_MOUNT%/}"
VAULT_KEY_NAME="${VAULT_KEY_NAME:-default}"
VAULT_SECRETS_DIR="${VAULT_SECRETS_DIR:-${SCRIPT_DIR}/.vault-secrets}"
VAULT_DEV_ROOT_TOKEN="${VAULT_DEV_ROOT_TOKEN:-dev-token}"

if ! command -v curl >/dev/null 2>&1; then
  err "curl not found."
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  err "jq not found. Install jq (apt-get/brew/winget install jq)."
  exit 1
fi
[[ -n "${VAULT_MOUNT}" ]] || { err "VAULT_MOUNT must not be empty."; exit 1; }

CURL=(curl -fsS --connect-timeout 5 --max-time 30)

IFS=',' read -r -a RAW_ADDRS <<< "${VAULT_ADDRESSES}"
ADDRS=()
for raw in "${RAW_ADDRS[@]}"; do
  raw="${raw//[[:space:]]/}"
  [[ -n "${raw}" ]] && ADDRS+=("${raw%/}")
done
[[ ${#ADDRS[@]} -ge 1 ]] || { err "VAULT_ADDRESSES is empty."; exit 1; }

# HTTP status without failing on non-2xx (health uses 429/501/503 as signals).
health_code() {
  curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 \
    "${1}/v1/sys/health" 2>/dev/null || echo 000
}

# --- step 1: reachability ----------------------------------------------------

log "waiting for ${#ADDRS[@]} vault node(s): ${ADDRS[*]}"
REACHABLE=0
for _ in $(seq 1 60); do
  REACHABLE=0
  for addr in "${ADDRS[@]}"; do
    code="$(health_code "${addr}")"
    [[ "${code}" != "000" ]] && REACHABLE=$((REACHABLE + 1))
  done
  [[ "${REACHABLE}" -eq ${#ADDRS[@]} ]] && break
  sleep 2
done
if [[ "${REACHABLE}" -ne ${#ADDRS[@]} ]]; then
  err "only ${REACHABLE}/${#ADDRS[@]} node(s) reachable — is the cluster up? (docker compose -f docker-compose.alpha.yml up -d vault1 vault2 vault3)"
  exit 1
fi
log "all nodes reachable"

# --- step 2: init ------------------------------------------------------------

KEYS_FILE="${VAULT_SECRETS_DIR}/unseal-keys.txt"
ROOT_TOKEN_FILE="${VAULT_SECRETS_DIR}/root-token.txt"
ENV_FRAG="${VAULT_SECRETS_DIR}/vault.env"

save_secret() {
  # mkdir -p + chmod are best-effort on Windows hosts (Git Bash / WSL).
  mkdir -p "${VAULT_SECRETS_DIR}" 2>/dev/null || true
  printf '%s\n' "$2" > "$1"
  chmod 600 "$1" 2>/dev/null || true
}

is_initialized() {
  # 501 = not initialized; everything else reachable = initialized (or dev mode).
  local code
  code="$(health_code "$1")"
  [[ "${code}" != "501" && "${code}" != "000" ]]
}

is_sealed() {
  # 503 = sealed, 501 = not initialized (sealed by definition).
  local code
  code="$(health_code "$1")"
  [[ "${code}" == "503" || "${code}" == "501" ]]
}

if is_initialized "${ADDRS[0]}"; then
  log "node1 already initialized — skipping init"
else
  log "initializing node1 ${ADDRS[0]} (Shamir key_shares=5, threshold=3)"
  resp="$("${CURL[@]}" -X POST "${ADDRS[0]}/v1/sys/init" \
    -H 'Content-Type: application/json' \
    -d '{"key_shares": 5, "secret_shares": 5, "secret_threshold": 3}')"
  root_token="$(jq -re '.root_token' <<<"${resp}")"
  mapfile -t keys < <(jq -re '.keys_base64[]' <<<"${resp}")
  [[ ${#keys[@]} -eq 5 ]] || { err "expected 5 unseal keys, got ${#keys[@]}"; exit 1; }
  save_secret "${KEYS_FILE}" "$(printf '%s\n' "${keys[@]}")"
  save_secret "${ROOT_TOKEN_FILE}" "${root_token}"
  log "root token + ${#keys[@]} unseal keys saved to ${VAULT_SECRETS_DIR}/ (gitignored)"
fi

# --- step 3: unseal all nodes ------------------------------------------------

ROOT_TOKEN="${VAULT_TOKEN:-}"
if [[ -z "${ROOT_TOKEN}" && -f "${ROOT_TOKEN_FILE}" ]]; then
  ROOT_TOKEN="$(head -n1 "${ROOT_TOKEN_FILE}" | tr -d '\r\n')"
fi
[[ -n "${ROOT_TOKEN}" ]] || ROOT_TOKEN="${VAULT_DEV_ROOT_TOKEN}"

unseal_node() {
  local addr="$1" i key
  is_sealed "${addr}" || { log "${addr} already unsealed"; return 0; }
  [[ -f "${KEYS_FILE}" ]] || { err "no unseal keys at ${KEYS_FILE} — cannot unseal ${addr}"; return 1; }
  log "unsealing ${addr} ..."
  while is_sealed "${addr}"; do
    # Feed keys until the threshold (3) is reached; re-running is harmless
    # because already-used keys are rejected with "duplicate" errors.
    for i in 1 2 3; do
      key="$(sed -n "${i}p" "${KEYS_FILE}" | tr -d '\r\n')"
      [[ -n "${key}" ]] || continue
      code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 \
        -X PUT "${addr}/v1/sys/unseal" -H 'Content-Type: application/json' \
        -d "{\"key\": \"${key}\"}" || echo 000)"
      [[ "${code}" == "200" ]] || warn "unseal attempt on ${addr} with key ${i} returned ${code}"
    done
    # Guard against an infinite loop when keys are wrong.
    if is_sealed "${addr}"; then
      local progress
      progress="$(curl -s --connect-timeout 5 --max-time 10 "${addr}/v1/sys/unseal-status" \
        | jq -r '.progress // 0' 2>/dev/null || echo '?')"
      if [[ "${progress}" == "0" ]]; then
        err "unseal of ${addr} reset to progress=0 — unseal keys mismatch (re-init?)"
        return 1
      fi
      sleep 2
    fi
  done
  log "${addr} unsealed"
}

unseal_node "${ADDRS[0]}"

# --- step 4: wait for leader, unseal the rest --------------------------------

log "waiting for an active Raft leader ..."
leader=""
for _ in $(seq 1 30); do
  for addr in "${ADDRS[@]}"; do
    if resp="$(curl -s --connect-timeout 5 --max-time 10 "${addr}/v1/sys/leader" 2>/dev/null)"; then
      leader="$(jq -r '.leader_api_address // empty' <<<"${resp}" 2>/dev/null || true)"
      [[ -n "${leader}" ]] && break
    fi
  done
  [[ -n "${leader}" ]] && break
  sleep 2
done
[[ -n "${leader}" ]] || warn "no leader reported yet — continuing (nodes may still be joining)"

for (( i=1; i<${#ADDRS[@]}; i++ )); do
  unseal_node "${ADDRS[i]}"
done

if [[ "${SKIP_ENGINE}" -eq 1 ]]; then
  log "done (--skip-engine): cluster is up, engine not touched"
  exit 0
fi

# --- step 5: enable secrets engine -------------------------------------------

auth_curl() {
  "${CURL[@]}" -H "X-Vault-Token: ${ROOT_TOKEN}" "$@"
}

active_addr=""
for addr in "${ADDRS[@]}"; do
  code="$(health_code "${addr}")"
  if [[ "${code}" == "200" ]]; then active_addr="${addr}"; break; fi
done
[[ -n "${active_addr}" ]] || active_addr="${ADDRS[0]}"
if [[ -n "${VAULT_TOKEN:-}" ]]; then
  using_token="provided via VAULT_TOKEN"
elif [[ -f "${ROOT_TOKEN_FILE}" ]]; then
  using_token="from ${ROOT_TOKEN_FILE}"
else
  using_token="dev-token"
fi

mount_type=""
code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 \
  -H "X-Vault-Token: ${ROOT_TOKEN}" "${active_addr}/v1/sys/mounts/${VAULT_MOUNT}" || echo 000)"
if [[ "${code}" == "200" ]]; then
  mount_type="$(auth_curl "${active_addr}/v1/sys/mounts/${VAULT_MOUNT}" | jq -r '.data.type // ""')"
  log "mount '${VAULT_MOUNT}' already exists (type=${mount_type})"
  if [[ "${FORCE_ENGINE}" -eq 1 ]]; then
    log "removing mount '${VAULT_MOUNT}' (--force-engine)"
    auth_curl -X DELETE "${active_addr}/v1/sys/mounts/${VAULT_MOUNT}" >/dev/null
    mount_type=""
  fi
fi

engine=""
if [[ -z "${mount_type}" ]]; then
  # Prefer the Tokenization engine (ADR-0007, Vault Enterprise);
  # fall back to transit with convergent encryption (open-source Vault).
  code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 \
    -H "X-Vault-Token: ${ROOT_TOKEN}" -H 'Content-Type: application/json' \
    -X POST "${active_addr}/v1/sys/mounts/${VAULT_MOUNT}" \
    -d '{"type": "tokenization"}' || echo 000)"
  if [[ "${code}" == "204" || "${code}" == "200" ]]; then
    engine="tokenization"
    log "enabled tokenization engine at '${VAULT_MOUNT}' (Vault Enterprise)"
  else
    warn "tokenization unavailable (HTTP ${code}) — open-source Vault? falling back to transit"
    code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 \
      -H "X-Vault-Token: ${ROOT_TOKEN}" -H 'Content-Type: application/json' \
      -X POST "${active_addr}/v1/sys/mounts/${VAULT_MOUNT}" \
      -d '{"type": "transit"}' || echo 000)"
    if [[ "${code}" == "204" || "${code}" == "200" ]]; then
      engine="transit"
      log "enabled transit engine at '${VAULT_MOUNT}' (convergent fallback)"
    else
      err "cannot enable secrets engine at '${VAULT_MOUNT}' (HTTP ${code})"
      exit 1
    fi
  fi
else
  case "${mount_type}" in
    *tokenization*) engine="tokenization" ;;
    *transit*)      engine="transit" ;;
    *) err "mount '${VAULT_MOUNT}' has unsupported type '${mount_type}'"; exit 1 ;;
  esac
fi

# --- step 6: key + engine config (idempotent) --------------------------------

if [[ "${engine}" == "tokenization" ]]; then
  # Best-effort tokenization key + template (Enterprise API, Prompt 31).
  code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 \
    -H "X-Vault-Token: ${ROOT_TOKEN}" -H 'Content-Type: application/json' \
    -X POST "${active_addr}/v1/${VAULT_MOUNT}/keys/${VAULT_KEY_NAME}" \
    -d '{"allowed_operations": ["encrypt", "decrypt", "verify"]}' || echo 000)"
  if [[ "${code}" == "200" || "${code}" == "204" ]]; then
    log "created tokenization key '${VAULT_KEY_NAME}'"
  elif [[ "${code}" == "400" ]]; then
    log "tokenization key '${VAULT_KEY_NAME}' already exists"
  else
    warn "cannot create tokenization key (HTTP ${code}) — check Enterprise license"
  fi
  code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 \
    -H "X-Vault-Token: ${ROOT_TOKEN}" -H 'Content-Type: application/json' \
    -X POST "${active_addr}/v1/${VAULT_MOUNT}/config" \
    -d '{"template": "sSSN_T_43"}' || echo 000)"
  [[ "${code}" == "204" || "${code}" == "200" ]] \
    && log "applied tokenization template (sSSN_T_43)" \
    || warn "cannot apply tokenization template (HTTP ${code})"
else
  # transit: convergent encryption => deterministic tokens for the same input.
  code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 \
    -H "X-Vault-Token: ${ROOT_TOKEN}" -H 'Content-Type: application/json' \
    -X POST "${active_addr}/v1/${VAULT_MOUNT}/keys/${VAULT_KEY_NAME}" \
    -d '{"type": "aes256-gcm96", "convergent_encryption": true, "exportable": false}' || echo 000)"
  if [[ "${code}" == "204" || "${code}" == "200" ]]; then
    log "created transit key '${VAULT_KEY_NAME}' (convergent, aes256-gcm96)"
  elif [[ "${code}" == "400" ]]; then
    log "transit key '${VAULT_KEY_NAME}' already exists"
  else
    err "cannot create transit key (HTTP ${code})"
    exit 1
  fi
fi

# --- summary -----------------------------------------------------------------

mkdir -p "${VAULT_SECRETS_DIR}" 2>/dev/null || true
cat > "${ENV_FRAG}" <<EOF
# Generated by backend/scripts/vault_init.sh — gitignored, do not commit.
# Add to <repo>/.env or export before: docker compose -f docker-compose.alpha.yml up
VAULT_TOKEN=${ROOT_TOKEN}
VAULT_MOUNT=${VAULT_MOUNT}
EOF
chmod 600 "${ENV_FRAG}" 2>/dev/null || true

log "done: engine='${engine}' mount='${VAULT_MOUNT}' key='${VAULT_KEY_NAME}' active=${active_addr}"
log "scanner env fragment: ${ENV_FRAG} (token ${using_token})"
log "verify: curl -s -H \"X-Vault-Token: \$VAULT_TOKEN\" ${active_addr}/v1/sys/mounts/${VAULT_MOUNT} | jq .data.type"
