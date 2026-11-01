# Contributing to OryaSecurity

Thanks for your interest in **OryaSecurity** (LLM Security Scanner)! 🛡️

This document describes our development process and the rules we follow. Before starting,
also read [README.md](./README.md), [analytics/ARCHITECT.md](./analytics/ARCHITECT.md) and
[analytics/MVP-PROMPTS.md](./analytics/MVP-PROMPTS.md) / [analytics/ALPHA-PROMPTS.md](./analytics/ALPHA-PROMPTS.md).

---

## 📑 Table of Contents

- [Code of Conduct](#-code-of-conduct)
- [Before you start](#-before-you-start)
- [Development environment](#-development-environment)
- [Workflow: branches and commits](#-workflow-branches-and-commits)
- [Code rules](#-code-rules)
- [Testing and coverage](#-testing-and-coverage)
- [Scan rules and Rego policies](#-scan-rules-and-rego-policies)
- [Security and PII](#-security-and-pii)
- [Pull Request process](#-pull-request-process)
- [CI/CD](#-cicd)
- [Commit style](#-commit-style)
- [Reporting issues](#-reporting-issues)
- [License](#-license)

---

## 🧭 Code of Conduct

Be respectful and constructive. We follow the principles of open collaboration:
professional communication, tolerance for different levels of expertise, and a focus on
technical arguments. Harassment, spam, and any disruptive behavior are not accepted.

---

## 🚀 Before you start

1. **Check existing issues and PRs** — your task may already be under discussion.
2. **Open an issue** for a new feature or significant change to agree on the approach
   before writing code (especially for architectural, ADR, or security-policy changes).
3. **Align the task with the roadmap**: [analytics/ROADMAP.md](./analytics/ROADMAP.md),
   [analytics/BACKLOG.md](./analytics/BACKLOG.md) and the MVP / ALPHA epics.
4. For changes that affect ADRs — update [analytics/ADR.md](./analytics/ADR.md) or
   create a new ADR in the PR.

---

## 🛠️ Development environment

Requirements:

- Python 3.12+
- Node.js 20+ (LTS)
- Docker + Docker Compose
- `uv` (recommended for backend) or `pip`
- Git (with GPG / SSH signing for policy commits)

### Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -e .
# or with uv:
uv sync --extra train --extra export   # for ML tasks

# Start dependencies (PostgreSQL + Redis)
docker compose up -d postgres redis

# Run the scanner in dev mode
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev     # http://localhost:3000
```

### Full ALPHA stack (for ML / Qdrant / OPA / Vault work)

```bash
docker compose -f docker-compose.alpha.yml up -d
```

---

## 🌿 Workflow: branches and commits

- **`main`** — stable, working branch. Direct pushes are forbidden.
- **Feature branches**: `feature/<short-name>` (e.g. `feature/streaming-block-handler`)
- **Bugfix branches**: `fix/<short-name>` (e.g. `fix/ssn-regex-false-positive`)
- **Documentation**: `docs/<topic>`
- **ML / Rego**: `ml/<task>` or `policy/<task>`

Rules:

- One PR — one logical change. Mixing refactoring with a new feature makes review harder.
- Rebase onto the latest `main` before opening a PR.
- Keep branches short-lived. For large tasks, split into several PRs.
- `main` is protected: **2 approvers** required, all CI checks green, branch up to date with `main`.

---

## 📐 Code rules

### Python (backend)

- **Type hints + Pydantic v2** for all API contracts and public functions.
- **asyncio / async I/O** in the hot path — no blocking calls without `run_in_executor`.
- **Structured JSON logging** via `structlog` (event string + kwargs); PII is always masked
  before logging (`redact()` before `logger.info(...)`).
- **Intermediate entities** — `dataclass` / Pydantic models, not bare dicts.
- **No sync I/O in the fast path** (Redis, Postgres, HTTP — async clients only).
- **Circuit breaker** on every external upstream (vector_db, ml_classifier, vault):
  modes `fail-closed` / `fail-open` / `degrade`.
- **Configuration** — via `app/config.py` (Pydantic Settings), env vars with defaults,
  secrets never in code.
- Imports use the style `from app.core.<module> import <name>` (absolute paths).

### TypeScript / Next.js (frontend)

- **Server components by default**, `"use client"` only where needed.
- **shadcn/ui components** for UI — don't write custom CSS unless necessary.
- **TanStack Query** for data fetching with proper loading/error states.
- **TypeScript strict mode**, no `any` without justification in a comment.
- Responsive layout (mobile-friendly).

### Style

- 4 spaces for Python, 2 for TypeScript/TSX.
- Line length — 100 characters (Python), 120 (TS).
- Run linters before committing:
  ```bash
  # Backend
  ruff check app tests
  ruff format app tests

  # Frontend
  cd frontend && npm run lint
  ```

---

## 🧪 Testing and coverage

**Requirement: ≥ 90% coverage for new code.** A PR that lowers overall coverage below 90%
won't be accepted without justification.

### Backend

```bash
cd backend

# Full suite with the coverage gate
python -m pytest --cov=app --cov-report=term-missing --cov-fail-under=90

# A single module (without the global gate — handy when working on a part)
uv run pytest tests/core/test_pdp.py -v --no-cov
uv run pytest tests/ml/test_batcher.py -v --no-cov

# Integration (requires Docker: PostgreSQL + Redis, optionally Qdrant)
uv run pytest tests/integration/ -v

# ML integration (requires a trained model in ml-models/classifier/v1/)
uv run pytest tests/ml/test_classifier_onnx.py -m integration -v --no-cov
```

### Frontend

```bash
cd frontend
npm run lint
npm run test          # vitest unit
npm run test:e2e      # Playwright E2E
npm run test:e2e:ui   # Playwright in UI mode
```

### Test rules

- **Pytest** + `pytest-asyncio` (`asyncio_mode="auto"`), `TestXxx` classes, parametrization.
- **Fixtures** in JSON for rules (5+ positive and 5+ negative examples per rule).
- **Mocks** via `unittest.mock` (patch external clients: Triton, Qdrant, Vault, OPA).
- **Integration tests** are marked `@pytest.mark.integration` and guarded with
  `skipif(not artifact.exists())`.
- For new endpoints — tests for: happy path, 401/403/422, edge cases, Pydantic contract.
- E2E — covers the full user journey (login → test scan → audit → rules).

---

## 📜 Scan rules and Rego policies

### YAML rules (`backend/rules/`)

When adding or changing regex rules:

1. Edit the relevant YAML (`pii.yaml`, `secrets.yaml`, or a new `custom.yaml`).
2. Follow the schema in `backend/app/core/rules/schema.json` (jsonschema).
3. Add 5+ positive and 5+ negative examples in `backend/tests/fixtures/`.
4. Parametrized unit tests in `backend/tests/core/rules/`.
5. Verify no false positives on ordinary numbers/strings.
6. Hot-reload will pick up changes without a restart, but add an integration test.

### Rego policies (`policies/`)

Rego policies are a critical security component with extra requirements:

- **Separate Git repository** for policies (or an isolated workflow with branch protection).
- **Signed commits** (GPG or SSH signing) — mandatory for `policies/`.
- **2-eyes review**: CODEOWNERS `policies/* @secops-team`, SecOps approval required.
- **Pre-commit hook**: `opa check` — Rego syntax validation.
- **CI**: `opa test` (unit tests), `opa fmt --fail` (formatting).
- **Versioning**: update `policies/manifest.json` (version, sha256, git_commit).
- **Bundle build**: via `scripts/build_opa_bundle.sh` — cosign-signed, atomic deploy.
- Every `deny[msg]` rule — with a clear message for the audit log.

```bash
# Local Rego checks
opa check policies/
opa fmt --fail policies/
opa test policies/
```

---

## 🔒 Security and PII

This project is about security, so the rules are stricter than usual:

- **Never log PII in clear text.** All PII goes through `redact()` before being written to
  logs/audit: SSN → `<SSN:HASH8>`, email → `<EMAIL:HASH8>`, etc.
- **No secrets in code.** API keys, passwords, JWT secrets — only via environment variables
  or Vault. Never commit `.env`.
- **`prompt_hash = SHA256(prompt)`** in logs and audit — never the raw prompt/generation
  without redaction.
- **API-key auth** on all endpoints except `/health` and `/metrics`.
- **Secrets in tests** — fakes only (`fakeredis`, mock clients). Real provider keys in tests
  are forbidden.
- **Dependencies** — update regularly, check for vulnerabilities (`pip-audit`, `npm audit`).
- **Container security**: non-root user, read-only rootfs where possible, minimal images.

### Reporting a vulnerability

If you find a security vulnerability in OryaSecurity itself — **do not open a public issue**.
Report it privately via GitHub Security Advisory
([Security tab](https://github.com/reider85/OryaSecurity/security/advisories/new)) or via
the email in the profile. We will respond within 72 hours.

---

## 🔀 Pull Request process

1. **Open an issue** and discuss the solution (for non-trivial changes).
2. **Create a branch** from the latest `main`: `git checkout -b feature/<name>`.
3. **Write code** with tests (see the sections above).
4. **Local checks** before pushing:
   ```bash
   # Backend
   cd backend
   ruff check app tests && ruff format --check app tests
   python -m pytest --cov=app --cov-fail-under=90

   # Frontend
   cd ../frontend
   npm run lint && npm run test
   ```
5. **Rebase** onto `main` if it moved ahead: `git rebase main`.
6. **Open a PR** with a description covering:
   - What changes and why (link to the issue).
   - Which ADRs are affected.
   - How to test (commands / steps).
   - Screenshots for UI — if the frontend changes.
   - Breaking changes — mark explicitly.
7. **CI must be green**: pytest, ruff, eslint, `opa check/test` (if policies are touched).
8. **2 approvers** (for `policies/` — SecOps from CODEOWNERS is mandatory).
9. **Squash & merge** into `main` (commit history in the PR is squashed).

### PR checklist

- [ ] Issue agreed (for non-trivial changes)
- [ ] Code follows project style (ruff / eslint green)
- [ ] Tests added, coverage ≥ 90%
- [ ] PII in logs masked
- [ ] No hardcoded secrets, `.env` not committed
- [ ] Documentation updated (README, docs/, ADR if needed)
- [ ] For `policies/`: `opa check` / `opa test` green, signed commit
- [ ] CHANGELOG note for a significant change

---

## ⚙️ CI/CD

CI runs on every PR (`.github/workflows/`):

- **Backend**: `ruff check`, `ruff format --check`, `pytest --cov-fail-under=90`,
  `pip-audit` (dependencies).
- **Frontend**: `eslint`, `tsc --noEmit`, `vitest`, `Playwright` (E2E smoke).
- **Policies**: `opa check`, `opa fmt --fail`, `opa test`, cosign-verify bundle.
- **ML**: when `ml-models/classifier/` changes — `evaluate.py` regression gate
  (F1 must not drop more than 5% from the baseline).
- **Docker**: build `backend` and `frontend` images, size check (< 200 MB for scanner).

A PR won't be accepted while any check is red. To re-run CI — `git push --force-with-lease`
an empty commit or click "Re-run" in Actions.

---

## 📝 Commit style

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**

| Type | Description |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `style` | Formatting, no logic change |
| `refactor` | Refactoring without behavior change |
| `perf` | Performance improvement |
| `test` | Adding / fixing tests |
| `chore` | Build, dependencies, CI |
| `ci` | CI configuration changes |
| `policy` | Rego policies (requires signed commit) |
| `ml` | ML models, training, export |

**Examples:**

```
feat(streaming): add chunk-based inspector for SSE blocks (EP-07.2)

Implements StreamingInspector with N=8 token buffer, cross-boundary
regex matching via pre-buffer (last 20 chars), and async post-factum
deep analysis hook. Closes #42.

Refs: analytics/ALPHA-PROMPTS.md EP-07 Prompt 2
```

```
fix(rules): narrow SSN regex to avoid matching phone numbers

Previous pattern \b\d{3}-\d{2}-\d{4}\b matched some 10-digit phone
formats. Adds boundary checks. Fixes #57.
```

```
policy(tenants): block prompt_injection with confidence > 0.85

Signed-off-by: Jane Doe <jane@example.com>
```

- **Subject** — imperative mood, ≤ 72 characters, lowercase first letter, no trailing period.
- **Body** — what and why (not how — the code shows that), wrapped at ≤ 100 characters per line.
- **Footer** — `Closes #N`, `Refs: ...`, `Signed-off-by:` (mandatory for policies).

---

## 🐛 Reporting issues

Before opening an issue:

1. Search existing issues — it may already be discussed.
2. Check [IMPLEMENTATION_STATUS.md](./IMPLEMENTATION_STATUS.md) — it may be known technical debt.
3. Reproduce on the latest `main`.

Issue template:

- **Description** — what happened, what you expected.
- **Steps to reproduce** — minimal scenario (commands / curl / UI actions).
- **Environment** — version (git SHA), Docker / local, OS, browser (for UI).
- **Logs / metrics** — relevant lines from structured logs (with `request_id`),
  without PII (mask it!).
- **Severity** — bug / feature request / security (for security — see the section above).

---

## 📄 License

By contributing to OryaSecurity, you agree that your Contributions are licensed under the
[Apache License 2.0](./LICENSE) (see Section 5 of the license: "Submission of Contributions").

- Don't commit code that is only compatible with a proprietary license.
- When adding third-party dependencies — check license compatibility with Apache-2.0
  (MIT, BSD, Apache-2.0, ISC — OK; GPL / AGPL — no).
- Preserve attribution notices of third-party code.

---

## 🙏 Thanks

Every contributor makes AI applications safer. Your name will appear in the repository
history and — for significant contributions — in the release notes of the relevant release.

<div align="center">

**Questions?** Open a [GitHub Discussion](https://github.com/reider85/OryaSecurity/discussions) or an issue.

</div>
