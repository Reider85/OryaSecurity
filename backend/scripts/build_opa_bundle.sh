#!/usr/bin/env bash
# Build, manifest, sign and publish the OPA policy bundle (EP-12 Prompt 28).
#
# Usage:
#   backend/scripts/build_opa_bundle.sh [VERSION] [--no-sign]
#
#   VERSION   semantic version for manifest.json (default: $POLICY_VERSION or "dev")
#   --no-sign skip cosign signing (local dev)
#
# Steps:
#   1. opa check --strict on policies/
#   2. stage .rego files (keeps manifest.json / built bundles OUT of the bundle)
#   3. opa build -> policies/bundles/bundle.tar.gz
#   4. gen_opa_manifest.py -> policies/manifest.json (+ copy next to the bundle)
#   5. cosign sign-blob -> bundle.tar.gz.sig / .cert (skipped if cosign missing)
#
# Output (docroot of the bundle-service, see docker-compose.alpha.yml):
#   policies/bundles/bundle.tar.gz
#   policies/bundles/bundle.tar.gz.sig
#   policies/bundles/bundle.tar.gz.cert
#   policies/bundles/manifest.json

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
POLICIES_DIR="${REPO_ROOT}/policies"
BUNDLE_DIR="${POLICIES_DIR}/bundles"
VERSION="${POLICY_VERSION:-dev}"
DO_SIGN=1

for arg in "$@"; do
  case "${arg}" in
    --no-sign) DO_SIGN=0 ;;
    -*) echo "unknown option: ${arg}" >&2; exit 2 ;;
    *) VERSION="${arg}" ;;
  esac
done

# --- resolve OPA binary (PATH -> backend/.bin) -------------------------------
OPA_BIN="${OPA:-}"
if [ -z "${OPA_BIN}" ]; then
  OPA_BIN="$(command -v opa 2>/dev/null || true)"
fi
if [ -z "${OPA_BIN}" ]; then
  for candidate in \
    "${REPO_ROOT}/backend/.bin/opa_linux_amd64_static" \
    "${REPO_ROOT}/backend/.bin/opa_darwin_arm64" \
    "${REPO_ROOT}/backend/.bin/opa_windows_amd64.exe"; do
    if [ -x "${candidate}" ] || [ -f "${candidate}" ]; then
      OPA_BIN="${candidate}"
      break
    fi
  done
fi
if [ -z "${OPA_BIN}" ]; then
  echo "OPA binary not found. Run: python backend/scripts/fetch_opa.py" >&2
  exit 1
fi
echo "[bundle] opa: $("${OPA_BIN}" version | head -1)"

# --- 1. validate -------------------------------------------------------------
echo "[bundle] opa check --strict"
"${OPA_BIN}" check --strict "${POLICIES_DIR}"

# --- 2. stage .rego files only ----------------------------------------------
# *_test.rego (Prompt 30) is excluded: unit tests never ship in a bundle.
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT
(cd "${POLICIES_DIR}" && find . -name '*.rego' -not -name '*_test.rego' -not -path './.*' -print0 |
  while IFS= read -r -d '' f; do
    mkdir -p "${STAGE}/$(dirname "${f}")"
    cp "${f}" "${STAGE}/${f}"
  done)

# --- 3. build ----------------------------------------------------------------
mkdir -p "${BUNDLE_DIR}"
echo "[bundle] opa build -> policies/bundles/bundle.tar.gz"
"${OPA_BIN}" build "${STAGE}" -o "${BUNDLE_DIR}/bundle.tar.gz"

# --- 4. manifest -------------------------------------------------------------
echo "[bundle] manifest (version=${VERSION})"
python "${POLICIES_DIR}/scripts/gen_opa_manifest.py" \
  --policies-dir "${POLICIES_DIR}" \
  --output "${POLICIES_DIR}/manifest.json" \
  --version "${VERSION}"
cp "${POLICIES_DIR}/manifest.json" "${BUNDLE_DIR}/manifest.json"

BUNDLE_SHA="$(sha256sum "${BUNDLE_DIR}/bundle.tar.gz" 2>/dev/null ||
              shasum -a 256 "${BUNDLE_DIR}/bundle.tar.gz" |
              awk '{print $1}')"
echo "[bundle] bundle sha256: ${BUNDLE_SHA}"

# --- 5. sign -----------------------------------------------------------------
if [ "${DO_SIGN}" = "1" ] && command -v cosign >/dev/null 2>&1; then
  echo "[bundle] cosign sign-blob (keyless)"
  cosign sign-blob --yes \
    --output-signature "${BUNDLE_DIR}/bundle.tar.gz.sig" \
    --output-certificate "${BUNDLE_DIR}/bundle.tar.gz.cert" \
    "${BUNDLE_DIR}/bundle.tar.gz"
else
  echo "[bundle] cosign not available or --no-sign: bundle left unsigned"
fi

echo "[bundle] done. Publish dir: ${BUNDLE_DIR}"
ls -la "${BUNDLE_DIR}"
