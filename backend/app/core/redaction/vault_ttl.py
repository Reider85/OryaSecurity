"""Token TTL / auto-cleanup for Vault-minted PII tokens (EP-13/Prompt 34,
ADR-0007).

Vault has no per-token TTL: neither the Tokenization engine nor the OSS
``transit`` fallback stores individual tokens with an expiry. The way to make
minted tokens die is **key version management**:

* ``POST /v1/{mount}/keys/{key}/rotate`` mints a new key version; every encode
  afterwards uses it (new ciphertext/token for the same plaintext);
* ``min_decryption_version`` (the field from Prompt 34's
  ``vault write tokenization/keys/default ... min_decryption_version=1``)
  blocks decryption of every older version — tokens minted under them stop
  decoding, i.e. they are effectively deleted.

The cleanup job rotates on an interval (default 300s = Prompt 34's 5 minutes)
and keeps the previous version decryptable (``grace_versions=1``), so an
in-flight request/stream that spans a rotation still decodes and every token
is guaranteed dead by the *next* rotation — a live time-to-live of
``(interval, 2*interval]`` (5–10 minutes by default). Inbound detokenization
is fail-open (Prompt 33), so an expired token degrades to a visible
placeholder — never a block and never a PII leak.

Design constraints (same contract as ``vault_client``):

* nothing here raises — every entry point returns a :class:`CleanupReport`;
* rotation/config writes go through :meth:`VaultClient.api` and do not touch
  the circuit breaker (maintenance, not the PII hot path);
* ``min_decryption_version`` is **monotonic** — it is never lowered, so a
  restarted job can never resurrect already-expired tokens.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.config import settings
from app.core.redaction.vault_client import VaultClient

logger = structlog.get_logger(__name__)

# Prompt 34: allowed_operations on the tokenization key (Enterprise only;
# the transit key has no such field).
ALLOWED_OPERATIONS = ["encrypt", "decrypt", "verify"]

# Baseline min_decryption_version applied by ensure_key_config (Prompt 34).
BASELINE_MIN_DECRYPTION_VERSION = 1


@dataclass
class KeyState:
    """Parsed key info: version numbers needed for TTL bookkeeping."""

    engine: str = ""
    latest_version: int = 0
    min_decryption_version: int = 0
    supported: bool = False
    error: str | None = None


@dataclass
class CleanupReport:
    """Outcome of one cleanup pass (ensure + rotate + min-version bump)."""

    ok: bool = False
    skipped: bool = False
    rotated: bool = False
    dry_run: bool = False
    engine: str = ""
    from_version: int | None = None
    to_version: int | None = None
    min_decryption_version: int | None = None
    reason: str | None = None

    @property
    def status(self) -> str:
        if self.ok:
            return "ok"
        if self.skipped:
            return "skipped"
        return "error"


def _err(body: dict, code: int, *, default: str) -> str:
    """Compact single-line error from a Vault response (mirror of the client's)."""
    if code == 0:
        return "vault_unreachable"
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        return f"{default}: " + "; ".join(str(e) for e in errors)[:300]
    return default


def _config_path(client: VaultClient, engine: str) -> str:
    """Where to write key config: tokenization folds it into the key itself
    (``vault write tokenization/keys/default ...``), transit uses /config.

    ``engine`` must be the *resolved* engine (``client.engine`` may be the
    ``auto`` placeholder, which would pick the wrong endpoint).
    """
    if engine == "tokenization":
        return f"/v1/{client.mount}/keys/{client.key_name}"
    return f"/v1/{client.mount}/keys/{client.key_name}/config"


def _config_body(engine: str, min_decryption_version: int) -> dict:
    if engine == "tokenization":
        return {
            "allowed_operations": list(ALLOWED_OPERATIONS),
            "min_decryption_version": min_decryption_version,
        }
    return {"min_decryption_version": min_decryption_version}


async def get_key_state(client: VaultClient) -> KeyState:
    """Read ``latest_version`` / ``min_decryption_version`` for the encode key.

    ``supported`` is False when the engine or the key does not expose version
    metadata — the caller then reports a skip instead of guessing.
    """
    engine = await client.resolve_engine()
    if engine is None:
        return KeyState(error="vault_engine_unavailable")

    code, body = await client.api("GET", f"/v1/{client.mount}/keys/{client.key_name}")
    if code == 0 or code < 200 or code >= 300:
        return KeyState(engine=engine, error=_err(body, code, default="vault_key_read_failed"))

    data = body.get("data") or {}
    latest = data.get("latest_version")
    if latest is None:
        # Some engines expose a versions map instead of latest_version.
        versions = data.get("versions")
        if isinstance(versions, dict) and versions:
            try:
                latest = max(int(v) for v in versions)
            except (TypeError, ValueError):
                latest = None
    min_dec = data.get("min_decryption_version")
    if latest is None or min_dec is None:
        return KeyState(engine=engine, error="vault_key_state_unsupported")

    return KeyState(
        engine=engine,
        latest_version=int(latest),
        min_decryption_version=int(min_dec),
        supported=True,
    )


async def ensure_key_config(client: VaultClient, *, dry_run: bool = False) -> CleanupReport:
    """Idempotent baseline key config (Prompt 34).

    Applies ``min_decryption_version=1`` (and tokenization
    ``allowed_operations``) only when the key does not report a valid config
    yet. **Never lowers** an existing ``min_decryption_version`` — that would
    resurrect tokens the TTL already killed.
    """
    state = await get_key_state(client)
    # No version metadata is not fatal here: the baseline config write below
    # is exactly what bootstraps the key config on first runs.
    if state.error and state.error != "vault_key_state_unsupported":
        return CleanupReport(engine=state.engine, reason=state.error, dry_run=dry_run)

    if state.supported and state.min_decryption_version >= BASELINE_MIN_DECRYPTION_VERSION:
        return CleanupReport(
            ok=True,
            engine=state.engine,
            min_decryption_version=state.min_decryption_version,
            reason="already_configured",
            dry_run=dry_run,
        )

    path = _config_path(client, state.engine)
    body = _config_body(state.engine, BASELINE_MIN_DECRYPTION_VERSION)
    if dry_run:
        return CleanupReport(
            ok=True,
            dry_run=True,
            engine=state.engine,
            min_decryption_version=BASELINE_MIN_DECRYPTION_VERSION,
            reason="dry_run",
        )

    code, resp = await client.api("POST", path, json_body=body)
    # 400 on the tokenization key means "already exists" (see vault_init.sh).
    if code == 400:
        return CleanupReport(ok=True, engine=state.engine, reason="already_exists", dry_run=dry_run)
    if code == 0 or code < 200 or code >= 300:
        return CleanupReport(
            engine=state.engine,
            reason=_err(resp, code, default="vault_key_config_failed"),
            dry_run=dry_run,
        )
    logger.info(
        "vault_key_configured",
        mount=client.mount,
        key=client.key_name,
        engine=state.engine,
        min_decryption_version=BASELINE_MIN_DECRYPTION_VERSION,
    )
    return CleanupReport(
        ok=True,
        engine=state.engine,
        min_decryption_version=BASELINE_MIN_DECRYPTION_VERSION,
        dry_run=dry_run,
    )


async def run_cleanup(
    client: VaultClient,
    *,
    grace_versions: int | None = None,
    dry_run: bool = False,
    ensure: bool = True,
) -> CleanupReport:
    """One TTL cleanup pass: rotate the encode key, retire older versions.

    ``grace_versions`` (default ``settings.vault_token_ttl_grace_versions``)
    is how many key versions stay decryptable after the rotation — with the
    default of 1 the previous window keeps decoding, so live requests are
    safe and tokens die by the next rotation.

    Never raises. ``skipped`` reports are not errors: unsupported engines or
    a missing rotate endpoint (Tokenization API variants) degrade to a WARN.
    """
    grace = (
        settings.vault_token_ttl_grace_versions if grace_versions is None else grace_versions
    )
    grace = max(0, grace)

    if ensure:
        ensured = await ensure_key_config(client, dry_run=dry_run)
        if not ensured.ok:
            return ensured

    state = await get_key_state(client)
    if state.error:
        # No version metadata -> skip: there is nothing we could rotate
        # safely. Real errors (unreachable, permission denied, ...) fail the
        # pass so the operator sees them.
        if state.error != "vault_key_state_unsupported":
            return CleanupReport(engine=state.engine, reason=state.error, dry_run=dry_run)
        return CleanupReport(
            engine=state.engine,
            skipped=True,
            reason=state.error,
            dry_run=dry_run,
        )

    if dry_run:
        return CleanupReport(
            ok=True,
            dry_run=True,
            engine=state.engine,
            from_version=state.latest_version,
            to_version=state.latest_version + 1,
            min_decryption_version=max(
                state.min_decryption_version, state.latest_version + 1 - grace
            ),
            reason="dry_run",
        )

    code, resp = await client.api(
        "POST", f"/v1/{client.mount}/keys/{client.key_name}/rotate", json_body={}
    )
    if code in (404, 405):
        # Rotate endpoint differs/absent (some Tokenization deployments) —
        # not fatal: report the skip, the caller logs a WARN.
        return CleanupReport(
            engine=state.engine, skipped=True, reason="rotation_unsupported", dry_run=dry_run
        )
    if code == 403:
        return CleanupReport(
            engine=state.engine, reason="rotation_forbidden", dry_run=dry_run
        )
    if code == 0 or code < 200 or code >= 300:
        return CleanupReport(
            engine=state.engine,
            reason=_err(resp, code, default="vault_rotate_failed"),
            dry_run=dry_run,
        )

    after = await get_key_state(client)
    if after.supported and after.latest_version > state.latest_version:
        new_version = after.latest_version
    else:
        new_version = state.latest_version + 1

    # Monotonic + grace: never lower min, keep `grace` versions alive.
    target_min = max(state.min_decryption_version, new_version - grace)

    code, resp = await client.api(
        "POST", _config_path(client, state.engine), json_body=_config_body(state.engine, target_min)
    )
    if code == 0 or code < 200 or code >= 300:
        # The rotation itself happened — old tokens are NOT retired yet.
        return CleanupReport(
            engine=state.engine,
            rotated=True,
            from_version=state.latest_version,
            to_version=new_version,
            reason=_err(resp, code, default="vault_min_version_update_failed"),
            dry_run=dry_run,
        )

    logger.info(
        "vault_token_ttl_rotation",
        mount=client.mount,
        key=client.key_name,
        engine=state.engine,
        from_version=state.latest_version,
        to_version=new_version,
        min_decryption_version=target_min,
        grace_versions=grace,
    )
    return CleanupReport(
        ok=True,
        engine=state.engine,
        rotated=True,
        from_version=state.latest_version,
        to_version=new_version,
        min_decryption_version=target_min,
        dry_run=dry_run,
    )


async def probe_roundtrip(client: VaultClient) -> str | None:
    """Encode/decode a harmless literal to prove the key still works.

    Returns an error string, or ``None`` on success. Log-only: the cleanup
    job never blocks anything on this result.
    """
    value = "ttl-probe"
    encoded = await client.encode(value, context="vault-ttl-probe")
    if not encoded.ok:
        return encoded.error or "vault_encode_failed"
    decoded = await client.decode(encoded.value, context="vault-ttl-probe")
    if not decoded.ok:
        return decoded.error or "vault_decode_failed"
    if decoded.value != value:
        return "vault_probe_mismatch"
    return None
