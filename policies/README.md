# LLM Security Policies

Rego-политики для OPA Policy Decision Point (ADR-0004) сканера OryaSecurity.
Этот каталог — **корень отдельного Git-репозитория** `github.com/org/llm-security-policies`
(сейчас живёт в монорепо `OryaSecurity/policies`, публикация — через `scripts/publish_repo.sh`).

Требования к репозиторию (Prompt 27):

- Branch protection `main`: **2 approvers**, code owner review, **signed commits**
- CODEOWNERS: все изменения политик одобряет SecOps
- Pre-commit hook: `opa check` + `opa fmt --fail` на staged `.rego`
- CI: `opa test` (unit tests), `opa fmt --fail` (format check)
- Релизы: теги `v1.0.0`, `v1.1.0`, … → bundle собирается и подписывается (cosign)

## Структура

```
policies/
├── .github/
│   ├── workflows/ci.yml    # validate + verify-commits + bundle (on tag)
│   └── CODEOWNERS
├── .githooks/pre-commit    # opa check --strict + opa fmt --fail
├── global/                 # политики, общие для всех tenant'ов
│   ├── default_actions.rego
│   └── owasp_top10.rego
├── tenants/                # per-tenant политики
│   └── default.rego
├── scripts/
│   ├── setup_branch_protection.sh
│   └── publish_repo.sh
├── Makefile
└── README.md
```

## Требования

- [OPA](https://www.openpolicyagent.org/) **0.68.0** (закреплённая версия; в монорепо —
  `python backend/scripts/fetch_opa.py`, бинарник в `backend/.bin/`)
- `make` (опционально — все цели дублируются прямыми командами `opa`)

## Команды

```sh
make              # fmt-check + check + test
make check        # opa check --strict .
make test         # opa test . -v
make fmt          # opa fmt -w .   (переписать в канонический формат)
make fmt-check    # opa fmt --fail .
make bundle       # opa build . -o bundle.tar.gz
make install-hooks  # git config core.hooksPath .githooks
```

Без `make`:

```sh
opa check --strict .
opa test . -v
opa fmt --fail .
```

В Windows без `make`/`opa` в PATH:

```powershell
$opa = "backend\.bin\opa_windows_amd64.exe"
& $opa check --strict policies/
& $opa test policies/
& $opa fmt --fail policies/
```

## Настройка локального окружения

```sh
# 1. OPA binary (монорепо)
python backend/scripts/fetch_opa.py

# 2. Pre-commit hook
cd policies && make install-hooks   # в монорепо: git config core.hooksPath policies/.githooks
```

### Signed commits (SSH)

Требование `main`: только подписанные коммиты. Настройка один раз на машину:

```sh
ssh-keygen -t ed25519 -C "oryasecurity-policy-signing" -f ~/.ssh/id_ed25519_policies -N ""

git config --local gpg.format ssh
git config --local user.signingkey ~/.ssh/id_ed25519_policies.pub
git config --local commit.gpgsign true
```

Затем добавьте `~/.ssh/id_ed25519_policies.pub` в GitHub → **Settings → SSH and GPG keys →
New SSH key → Signing Key** (тип обязателен — обычный SSH key не делает коммит «Verified»).

Проверка: `git commit -S ...`, потом `git log --show-signature -1`.

### Branch protection (однократно, admin-репозитория)

```sh
policies/scripts/setup_branch_protection.sh org/llm-security-policies
```

Применяет: 2 required approvals, code owner review, signed commits, required status checks.
Замените `@secops-team` в `.github/CODEOWNERS` на реальный team/user handle **до** включения
«Require review from Code Owners».

> Caveat: на GitHub Free branch protection доступен только для public-репозиториев.
> Для private-репо используйте Rulesets или платный план.

CI дополнительно проверяет подписи коммитов независимо от настроек репозитория
(job `verify-commits`: `.commit.verification.verified == true`).

## Релизы (versioned bundle)

1. Измените политики, соберите PR, получите 2 approver'а (коммиты подписаны).
2. Слейте в `main`, поставьте тег и запушьте:

   ```sh
   git tag v1.0.0
   git push origin v1.0.0
   ```

3. CI (`bundle` job):
   - `opa build . -o bundle.tar.gz`
   - `cosign sign-blob --yes` (keyless, OIDC → Fulcio + Rekor)
   - артефакты `bundle.tar.gz`, `bundle.tar.gz.sig`, `bundle.tar.gz.cert`
     → GitHub Release `v1.0.0`

Проверка подписи:

```sh
cosign verify-blob bundle.tar.gz \
  --signature bundle.tar.gz.sig \
  --certificate bundle.tar.gz.cert \
  --certificate-identity "https://github.com/org/llm-security-policies/.github/workflows/ci.yml@refs/tags/v1.0.0" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --insecure-ignore-tlog
```

В монорепо теги политик имеют префикс `policies-v*` (например `policies-v1.0.0`),
чтобы не конфликтовать с релизами приложения.

## Публикация отдельного репозитория

```sh
# создаёт локальную ветку из префикса policies/ и пушит как main нового репо
gh repo create org/llm-security-policies --private --description "LLM security Rego policies"
policies/scripts/publish_repo.sh git@github.com:org/llm-security-policies.git
```

## CI в монорепо

Внутри `OryaSecurity` GitHub **не** запускает `.github/workflows` из подкаталогов,
поэтому проверки продублированы в [`.github/workflows/policies-ci.yml`](../.github/workflows/policies-ci.yml)
(path filter: `policies/**`, теги `policies-v*`). Содержимое обоих workflow синхронизировано
руками — при изменении одного обновите второй.

## Связанные документы

- `analytics/ALPHA-PROMPTS.md` — Промпт 27 (EP-12: OPA Policy Decision Point)
- `analytics/ADR.md` — ADR-0004 (OPA + Rego), ADR-0011 (мультиарендность, per-tenant bundles)
- `docs/opa-bundles.md` — bundle distribution (Промпт 25/28)
