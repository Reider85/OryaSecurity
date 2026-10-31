#!/usr/bin/env bash
set -euo pipefail

# Self-signed TLS certificate для Envoy ingress (EP-35, Prompt 46, ADR-0001).
#
# Flow (идемпотентен — безопасен для повторного запуска):
#   1. создать <repo>/certs/ если отсутствует
#   2. пропустить, если scanner.crt + scanner.key уже есть (без --force)
#   3. openssl: RSA-2048, SAN localhost/127.0.0.1/envoy/scanner, 365 дней
#
# Результат (gitignored, см. certs/.gitignore):
#   certs/scanner.crt  — certificate chain (монтируется Envoy в /etc/ssl/)
#   certs/scanner.key  — private key, mode 600
#
# Usage:
#   bash backend/scripts/gen_certs.sh [--force]
#
# Environment (переопределяют дефолты):
#   CERTS_DIR   Куда писать сертификаты      [<repo>/certs]
#   CERT_CN     Subject CN                    [localhost]
#   CERT_DAYS   Срок действия в днях          [365]
#   CERT_EXTRA_SANS  Доп. SAN через запятую   [пусто]
#                    (например: "DNS:myhost,IP:10.0.0.5")
#
# В production используйте корпоративный PKI / ACME (Let's Encrypt),
# self-signed сертификат — только для dev/staging.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log()  { echo "[gen-certs] $*"; }
err()  { echo "[gen-certs] ERROR: $*" >&2; }
warn() { echo "[gen-certs] WARN: $*"; }

usage() {
  cat <<'EOF'
Generate self-signed TLS cert for the Envoy ingress (dev/staging).

Usage: gen_certs.sh [options]

Options:
  --force     Re-create certificates even if they already exist.
  -h, --help  Show this help.

Environment (defaults in brackets):
  CERTS_DIR          Output directory [<repo>/certs]
  CERT_CN            Certificate CN [localhost]
  CERT_DAYS          Validity in days [365]
  CERT_EXTRA_SANS    Extra SAN entries, comma-separated [empty]
                     e.g. "DNS:myhost.example.com,IP:10.0.0.5"
                     (DNS:localhost, DNS:envoy, DNS:scanner, IP:127.0.0.1 always included)

NEVER commit the generated files — certs/ is gitignored.
EOF
}

FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "unknown option: $1"; usage >&2; exit 2 ;;
  esac
done

# Optional .env loader (same convention as vault_init.sh)
if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

CERTS_DIR="${CERTS_DIR:-${ROOT_DIR}/certs}"
CERT_CN="${CERT_CN:-localhost}"
CERT_DAYS="${CERT_DAYS:-365}"
CERT_EXTRA_SANS="${CERT_EXTRA_SANS:-}"

CRT="${CERTS_DIR}/scanner.crt"
KEY="${CERTS_DIR}/scanner.key"

# --- prerequisites -----------------------------------------------------------
if ! command -v openssl >/dev/null 2>&1; then
  err "openssl not found in PATH."
  err "Install OpenSSL, or run via Docker:"
  err "  docker run --rm -v \"${CERTS_DIR}:/certs\" alpine/openssl req -x509 ..."
  exit 1
fi

mkdir -p "${CERTS_DIR}"

if [[ "${FORCE}" -eq 0 && -f "${CRT}" && -f "${KEY}" ]]; then
  # Проверяем, что сертификат ещё валиден (>7 дней)
  if openssl x509 -checkend 604800 -noout -in "${CRT}" >/dev/null 2>&1; then
    log "certificates already exist and are valid (>7 days left): ${CRT}"
    log "use --force to re-create"
    exit 0
  fi
  warn "existing certificate expires in less than 7 days — re-creating"
fi

# --- build SAN list ----------------------------------------------------------
SANS="DNS:localhost,DNS:envoy,DNS:scanner,IP:127.0.0.1"
if [[ -n "${CERT_EXTRA_SANS}" ]]; then
  SANS="${SANS},${CERT_EXTRA_SANS}"
fi

log "generating self-signed certificate (CN=${CERT_CN}, days=${CERT_DAYS})"
log "SANs: ${SANS}"

openssl req -x509 -newkey rsa:2048 -sha256 -days "${CERT_DAYS}" -nodes \
  -keyout "${KEY}" \
  -out "${CRT}" \
  -subj "/CN=${CERT_CN}/O=llm-security-scanner/OU=dev" \
  -addext "subjectAltName=${SANS}" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" \
  >/dev/null 2>&1

chmod 600 "${KEY}"
chmod 644 "${CRT}"

log "wrote ${CRT}"
log "wrote ${KEY} (mode 600)"
log "subject: $(openssl x509 -noout -subject -in "${CRT}" | sed 's/^subject=//')"
log "valid until: $(openssl x509 -noout -enddate -in "${CRT}" | sed 's/^notAfter=//')"
log "done — restart envoy to pick up the new certificate:"
log "  docker compose -f docker-compose.yml -f docker-compose.alpha.yml restart envoy"
