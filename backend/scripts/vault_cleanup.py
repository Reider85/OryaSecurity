#!/usr/bin/env python3
"""Vault token TTL / auto-cleanup job (EP-13/Prompt 34, ADR-0007).

Rotates the Vault encode key on an interval (default 5 minutes) and retires
older key versions through ``min_decryption_version``, so PII tokens minted by
the redactor (Prompt 32) stop decoding once the TTL window passes. Vault has no
per-token expiry — key versions *are* the TTL; see
``app/core/redaction/vault_ttl.py`` for the mechanics and the grace-window
rationale (live TTL = (interval, 2*interval]).

Usage:
    python scripts/vault_cleanup.py --once     # single pass (cron/systemd)
    python scripts/vault_cleanup.py --loop     # daemon, every --interval seconds

Environment (``app.config`` settings, env prefix SCANNER_):
    SCANNER_VAULT_URL                    Vault base URL; empty = not configured
    SCANNER_VAULT_TOKEN                  X-Vault-Token (root/app token)
    SCANNER_VAULT_MOUNT                  secrets engine mount [tokenization]
    SCANNER_VAULT_KEY_NAME               key within the engine [default]
    SCANNER_VAULT_TOKEN_TTL_SECONDS      rotation interval [300]
    SCANNER_VAULT_TOKEN_TTL_GRACE_VERSIONS  key versions kept alive [1]

Exit codes: 0 = success or skip, 1 = failure (--once only), 2 = Vault not
configured. Transient errors inside --loop are logged and retried, never fatal.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Sequence

# Run as a plain file (`python scripts/vault_cleanup.py`) from any CWD: put the
# backend/ package root on sys.path when `app` is not installed (the container
# image installs it editable, local checkouts usually do not).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import structlog

from app.config import settings
from app.core.redaction.vault_client import VaultClient
from app.core.redaction.vault_ttl import probe_roundtrip, run_cleanup

logger = structlog.get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault_cleanup.py",
        description=(
            "Rotate the Vault encode key and retire old versions so minted "
            "PII tokens expire (EP-13/Prompt 34)."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        action="store_true",
        help="run a single cleanup pass and exit (default).",
    )
    mode.add_argument(
        "--loop",
        action="store_true",
        help="run forever, one pass every --interval seconds.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=settings.vault_token_ttl_seconds,
        metavar="SECONDS",
        help="seconds between rotations in --loop mode "
        "[default: SCANNER_VAULT_TOKEN_TTL_SECONDS=%(default)s].",
    )
    parser.add_argument(
        "--grace-versions",
        type=int,
        default=settings.vault_token_ttl_grace_versions,
        metavar="N",
        help="key versions kept decryptable after a rotation "
        "[default: SCANNER_VAULT_TOKEN_TTL_GRACE_VERSIONS=%(default)s].",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would rotate; no write requests are sent.",
    )
    parser.add_argument(
        "--ensure-config",
        dest="ensure_config",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="apply the baseline key config (min_decryption_version) first "
        "(default: enabled).",
    )
    parser.add_argument(
        "--verify",
        dest="verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="after a successful pass, encode/decode a probe value (default: enabled).",
    )
    parser.add_argument(
        "--vault-url",
        default=None,
        help="override SCANNER_VAULT_URL.",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="override SCANNER_VAULT_TOKEN.",
    )
    parser.add_argument(
        "--mount",
        default=None,
        help="override SCANNER_VAULT_MOUNT.",
    )
    parser.add_argument(
        "--key",
        default=None,
        help="override SCANNER_VAULT_KEY_NAME.",
    )
    return parser


def _make_client(args: argparse.Namespace) -> VaultClient:
    return VaultClient(
        vault_url=args.vault_url if args.vault_url is not None else settings.vault_url,
        token=args.token if args.token is not None else settings.vault_token,
        mount=args.mount if args.mount is not None else settings.vault_secret_mount,
        key_name=args.key if args.key is not None else settings.vault_key_name,
    )


async def run_once(
    client: VaultClient,
    *,
    grace_versions: int | None = None,
    dry_run: bool = False,
    ensure: bool = True,
    verify: bool = True,
) -> int:
    """Single cleanup pass. Returns the process exit code (0 ok/skip, 1 error)."""
    report = await run_cleanup(
        client, grace_versions=grace_versions, dry_run=dry_run, ensure=ensure
    )
    fields = {
        "status": report.status,
        "engine": report.engine or None,
        "from_version": report.from_version,
        "to_version": report.to_version,
        "min_decryption_version": report.min_decryption_version,
        "dry_run": report.dry_run,
        "reason": report.reason,
    }
    if report.status == "error":
        logger.error("vault_cleanup_failed", **fields)
        return 1
    if report.status == "skipped":
        logger.warning("vault_cleanup_skipped", **fields)
        return 0

    if verify and not dry_run:
        probe_error = await probe_roundtrip(client)
        if probe_error:
            # Log-only: the rotation succeeded, the probe tells the operator
            # the engine may need attention — never blocks anything.
            logger.warning("vault_cleanup_probe_failed", error=probe_error)
        else:
            logger.info("vault_cleanup_probe_ok")

    logger.info("vault_cleanup_pass", **fields)
    return 0


async def _loop(client: VaultClient, args: argparse.Namespace) -> int:
    interval = max(1, args.interval)
    stop = asyncio.Event()

    def _handler(*_sig_args: object) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (NotImplementedError, OSError, ValueError):  # pragma: no cover
            pass

    logger.info(
        "vault_cleanup_loop_started",
        interval_seconds=interval,
        grace_versions=args.grace_versions,
        vault_url=client.vault_url,
        mount=client.mount,
        key=client.key_name,
    )
    while not stop.is_set():
        try:
            await run_once(
                client,
                grace_versions=args.grace_versions,
                dry_run=args.dry_run,
                ensure=args.ensure_config,
                verify=args.verify,
            )
        except Exception:  # pragma: no cover - loop must never die
            logger.exception("vault_cleanup_pass_crashed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    logger.info("vault_cleanup_loop_stopped")
    return 0


async def _amain(args: argparse.Namespace) -> int:
    client = _make_client(args)
    try:
        if args.loop:
            return await _loop(client, args)
        return await run_once(
            client,
            grace_versions=args.grace_versions,
            dry_run=args.dry_run,
            ensure=args.ensure_config,
            verify=args.verify,
        )
    finally:
        await client.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.vault_url or settings.vault_url):
        logger.error(
            "vault_cleanup_disabled",
            reason="vault_url is empty — set SCANNER_VAULT_URL",
        )
        return 2
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
