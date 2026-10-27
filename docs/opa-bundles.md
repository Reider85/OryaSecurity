# OPA Bundle Distribution (EP-12, Prompts 25 + 28)

Как собираются, подписываются и доставляются версионированные Rego-политики
до OPA в ALPHA-стеке.

## Архитектура

```
git tag policies-vX.Y.Z
   │
   ▼  CI (.github/workflows/policies-ci.yml, job "bundle")
build_opa_bundle.sh X.Y.Z
   ├─ opa check --strict
   ├─ opa build  (staging: только *.rego)
   ├─ gen_opa_manifest.py  → policies/manifest.json
   ├─ cosign sign-blob     → bundle.tar.gz.sig / .cert
   └─ публикация           → policies/bundles/  (docroot bundle-service)
   │
   ▼  nginx (bundle-service :8081) отдаёт /bundles/*
OPA (:8181) poll'ит /bundles/bundle.tar.gz каждые 30 сек
   │  атомарная активация нового bundle — без прерывания запросов
   ▼
scanner → OPAClient.evaluate(input) → /v1/data/tenant_default
scanner → OPAClient.get_active_policy_version() → /bundles/manifest.json
        → audit_events.policy_version
```

## Manifest: `policies/manifest.json`

Генерируется скриптом (не редактируется руками):

```json
{
  "version": "1.0.0",
  "bundles": [
    {"id": "global", "path": "global/", "sha256": "..."},
    {"id": "tenants_default", "path": "tenants/default.rego", "sha256": "..."}
  ],
  "created_at": "2026-09-27T10:00:00Z",
  "git_commit": "abc123..."
}
```

- `version` — semver из git tag (`policies-v1.2.3` → `1.2.3`), для локальной
  сборки — `dev` или значение `$POLICY_VERSION`.
- `sha256` — детерминированный хеш содержимого bundle (по отсортированным
  путям и байтам `.rego`-файлов): смена политики меняет sha256.
- `git_commit` — HEAD сборки (для трассировки).

## Локальная сборка

```bash
# сборка + manifest + подпись (если установлен cosign)
backend/scripts/build_opa_bundle.sh 1.0.0

# без подписи (dev)
backend/scripts/build_opa_bundle.sh --no-sign

# только manifest
python policies/scripts/gen_opa_manifest.py --policies-dir policies --version 1.0.0
```

Результат — содержимое `policies/bundles/` (это и есть docroot bundle-service):

| Файл | Назначение |
|---|---|
| `bundle.tar.gz` | OPA bundle (`opa build` из staging c только `.rego`) |
| `manifest.json` | активная версия (её читает scanner) |
| `bundle.tar.gz.sig`, `.cert` | cosign-подпись (provenance) |

В staging собираются **только** `.rego`: `manifest.json` и старые bundle'ы
никогда не попадают внутрь нового bundle (рекурсия/данные).

## CI/CD (on tag push)

1. Push тега `policies-vX.Y.Z` → job `bundle` в `.github/workflows/policies-ci.yml`.
2. `build_opa_bundle.sh X.Y.Z` → build + manifest + `cosign sign-blob`
   (keyless, OIDC→Fulcio).
3. Артефакты → GitHub Release + workflow artifact.
4. Опционально: задайте repository variable `BUNDLE_UPLOAD_URL`
   (HTTP PUT endpoint вида `https://bundle.internal/bundles/`) — тогда bundle
   и manifest публикуются на bundle-service автоматически. Без переменной шаг
   пропускается (ручная публикация: скопировать файлы в `policies/bundles/`).
5. OPA polls каждые 30s (`bundles.scanner.polling.*_delay_seconds=30`),
   новый bundle активируется **атомарно** — in-flight запросы не прерываются.

Аналогичный job в standalone-репо: `policies/.github/workflows/ci.yml`
(теги `v*`, сборка из `dist/`).

## Проверка подписи

```bash
cosign verify-blob \
  --certificate bundle.tar.gz.cert \
  --signature bundle.tar.gz.sig \
  --certificate-identity-regexp '.*' \
  --certificate-oidc-issuer https://github.com \
  bundle.tar.gz
```

Верификация bundle'а внутри самого OPA (OIDC/секреты) — BETA, на ALPHA
подпись служит provenance-артефактом в Release.

## Runtime-поведение scanner

- `SCANNER_PDP_OPA_ENABLED=true`, `SCANNER_OPA_URL=http://opa:8181`,
  `SCANNER_BUNDLE_SERVICE_URL=http://bundle-service:8081`
  (заданы в `docker-compose.alpha.yml`).
- `OPAClient.get_active_policy_version()` — `GET /bundles/manifest.json`,
  in-process кеш 30s (`SCANNER_POLICY_VERSION_CACHE_TTL_SECONDS`), fallback →
  `settings.policy_version`. Значение попадает в `audit_events.policy_version`
  и в ключ decision cache (`sha256(version:prompt)`), поэтому смена bundle
  автоматически инвалидирует закешированные вердикты.
- `OPAClient.evaluate()` — `POST /v1/data/tenant_default` c input-документом
  `{policy_version, tenant_id, request_id, detectors:{rules, pii, ml_classifier}}`.
  Timeout 200ms (`SCANNER_OPA_TIMEOUT_MS`), circuit breaker (5 ошибок → open
  30s).
- OPA недоступна → fail-open: локальный rules engine + метрика
  `scanner_pdp_opa_errors_total{type=...}`; при `SCANNER_PDP_OPA_FAIL_OPEN=false`
  → BLOCK «PDP unavailable (fail-closed)».
- Merge: OPA `deny` → BLOCK; локальные block/high-severity правила → BLOCK
  независимо от ответа OPA (defense in depth).

## Релизы / roll-back

- Новая версия = новый git tag → новый bundle в Release.
- Roll-back: переиздать bundle из старого тега
  (`git checkout policies-vX.Y.Z && backend/scripts/build_opa_bundle.sh X.Y.Z`)
  и скопировать в `policies/bundles/` — OPA подхватит в течение 30s.
- История версий: git tags + `manifests` в Release.

Связанные ADR: [ADR-0004](../analytics/adr-final/0004-policy-decision-point-opa.md),
[ADR-0008](../analytics/adr-final/0008-circuit-breaker-fallback.md).
