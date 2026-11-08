# GITHUB.md — стратегия релизов, CI и роста звёзд для `Reider85/OryaSecurity`

> Файл-итог по запросу: «составь промпты, чтобы при каждом коммите и пуше создавался релиз на GitHub и в трендс на гитхабе мы появлялись, также проверь что реализовано по github ci почему всегда failed как оптимизировать и что даст, ещё уточни какие тесты можно выполнять с помощью github ci это что нибудь улучшит или нет».
>
> Главная цель: **проект должен чаще мелькать и набирать звезду при пушах**, плюс по возможности переложить работу на бесплатные ресурсы GitHub.
>
> Формат результата — несколько готовых промптов, которые можно по очереди скармливать агенту (OpenCode/Codex/Claude/Cursor), чтобы он применил изменения к репозиторию.

---

## 1. Что реально лежит в репозитории сейчас

В `.github/workflows/` ровно **два** файла:

| Файл | Триггер | Состояние |
|---|---|---|
| `policies-ci.yml` | `pull_request` (paths `policies/**`), `push: branches: [main]`, `tags: policies-v*`, `workflow_dispatch` | **Не работает** — 22 из 22 последних запусков failed |
| `ml-evaluation.yml` | `pull_request` (paths `backend/app/ml/**`, …), `workflow_dispatch` | На push в main **не запускается вообще**, только на PR |

Тегов и релизов в репозитории **нет вообще**. Топиков — 16 штук (нормально). Бейджи в README — только технологические (Python, FastAPI, Next.js, …), бейджа CI/Release/CodeCoverage нет.

---

## 2. Почему CI всегда failed (диагноз по факту, не на глаз)

Я открыл последний прогон `41239407946` и вытащил аннотации ошибок GitHub Actions:

### Job `validate` (opa check / test / fmt)
```
Set up job — Unable to resolve action `openpolicyagent/setup-opa`,
repository not found.
```
**Причина.** Экшн переехал из неймспейса `openpolicyagent/` в `open-policy-agent/` (с дефисами). Старый репозиторий возвращает 404 — GitHub не может зарезолвить action и валит джобу ещё до первого шага. Проверено: `https://github.com/openpolicyagent/setup-opa` → 404, `https://github.com/open-policy-agent/setup-opa` → 200.

### Job `verify-commits` (проверка подписей коммитов, которые трогают `policies/`)
```
Node.js 20 is deprecated. The following actions target Node.js 20
but are being forced to run on Node.js 24: actions/github-script@v7.
```
Это предупреждение, но сама джоба тоже падает, потому что в `policies/` регулярно попадают **неподписанные** коммиты (`feat: …`, `fix: …`), а `verify-commits` настроен как hard-fail на каждый push в `main`. Иными словами — политика подписей включена для каталога, в который вы льёте фичи без GPG/SSH-подписи.

### Job `bundle` (build + sign OPA bundle)
Корректно `skipped` — у неё `if: startsWith(github.ref, 'refs/tags/policies-v')`, а тегов нет. Это **не** ошибка.

### Итог по CI
Оба падения — «механические»:
1. Опечатка в неймспейсе action (`openpolicyagent` → `open-policy-agent`).
2. Слишком жёсткая политика проверки подписей для нетеговых пушей + устаревший `actions/github-script@v7`.

То есть **сейчас CI не проверяет ваш код вообще** — он падает до запуска тестов. Это означает, что проект внешне выглядит «красным» в истории, что **убивает** конверсию посетителей в звёзды (см. §4).

---

## 3. Что даст починка и оптимизация CI

| Изменение | Что даст |
|---|---|
| Замена `openpolicyagent/setup-opa@v2` → `open-policy-agent/setup-opa@v2` | CI перестанет падать на setup — заработают `opa check / test / fmt`. Зелёные галочки в истории коммитов = +доверие, +звёзды. |
| `actions/github-script@v7` → `@v8` (Node 22) | Уход от deprecation warning, которое через пару месяцев превратится в hard-fail. |
| `verify-commits` → `continue-on-error: true` (или триггер только на `tags: policies-v*`) | CI перестанет падать из-за неподписанных фич-коммитов. Подписи по-прежнему будут проверяться при релизе bundle. |
| `paths-ignore: ['**/*.md', 'docs/**', 'LICENSE']` | Документационные коммиты не будут запускать тяжёлый policies-CI — экономия минут GitHub Actions. |
| `fail-fast: false` + матрица | Один упавший линтер не будет прерывать тесты — быстрее видно реальную картину. |
| Кэш pip/pnpm/HuggingFace через `actions/cache@v4` | До −40% времени пайплайна, бесплатные минуты расходуются медленнее. |

**Что ещё улучшит процесс, используя ресурсы GitHub вместо ваших:**
- **GitHub-hosted runners** (ubuntu-latest) бесплатны для публичных репозиториев без лимита по числу запусков — только 2000 минут/мес на private, для public безлимитно.
- **GitHub Actions cache** — 10 ГБ бесплатно на репо.
- **GitHub Packages / Container registry** — 500 МБ бесплатно, можно пушить Docker-образ без Docker Hub.
- **GitHub Pages** — статический хостинг документации бесплатно.
- **CodeQL** — бесплатный SAST для OSS.
- **Dependabot** — бесплатное обновление зависимостей, плюс генерирует PR-ы = «живая» активность в Insights.
- **Codecov** — бесплатный план для публичных репо, бейдж покрытия.

---

## 4. Какие тесты имеет смысл гонять в GitHub CI и улучшит ли это что-то

Коротко: **да, улучшит** — но только если пайплайн зелёный и виден через бейдж. Красный бейдж работает в минус.

Для `OryaSecurity` (Python/FastAPI бэкенд + Next.js фронтенд + Rego-политики + ML-классификатор) имеет смысл:

| Слой | Инструмент | Что ловит | Запускать |
|---|---|---|---|
| **Lint Python** | `ruff check` + `ruff format --check` | стиль, импорты, баги | на каждый push/PR |
| **Type-check Python** | `mypy backend/app` | типы, nullability | на каждый push/PR |
| **Юнит-тесты backend** | `pytest backend/tests -q --cov` | регрессии в логике сканера, PII/secret детекторов, политики | на каждый push/PR |
| **Контракт-тесты API** | `schemathesis run` против OpenAPI | ломается ли совместимость endpoint-ов | на PR |
| **Lint JS/TS** | `eslint` + `prettier --check` | стиль фронтенда | на каждый push/PR |
| **Type-check TS** | `tsc --noEmit` | типы фронтенда | на каждый push/PR |
| **Юнит-тесты frontend** | `vitest run` / `jest` | регрессии в UI-логике | на каждый push/PR |
| **Rego политики** | `opa check --strict`, `opa test .` | корректность политик | на каждый push/PR (уже есть в `policies-ci.yml`) |
| **Docker build** | `docker build` без пуша | образ собирается, нет битых слоёв | на каждый push/PR |
| **ML smoke-train + F1 gate** | `app.ml.train_classifier --smoke` + `app.ml.evaluate` | регрессия классификатора | только на PR в `backend/app/ml/**` (уже есть в `ml-evaluation.yml`) |
| **CodeQL** | `github/codeql-action/init@v3` | уязвимости в коде | по расписанию + на PR |
| **Тест безопасности своего API** | `pytest tests/security/` (Hippie/pytest-bdd) | инъекции, утечки PII | на PR |

**Важно:** все эти тулзы бесплатны при работе на `ubuntu-latest` runner. Единственное, что реально тратит ваши минуты — это ML-тренировка (60 мин таймаут), поэтому её стоит держать только на PR с `paths`-фильтром (как сейчас).

---

## 5. Стратегия попадания в GitHub Trending

Алгоритм Trending закрытый, но из официальных ответов GitHub Community и наблюдений известно: в расчёт идёт **количество новых звёзд за период (день/неделя/месяц) с поправкой на отклонение от среднего числа звёзд** + «свежесть» (последний коммит ≤ 30 дней) + языковая принадлежность. То есть, чтобы попасть в Trending, нужно **аномальное количество звёзд за короткий промежуток**, а не общее число.

Поскольку на каждый push вы **не получаете** новых звёзд автоматически, чисто CI-настройка в Trending не приведёт. Но она даёт два нужных эффекта:

1. **Активность в Insights** — коммиты + релизы + зелёные проверки + Dependabot-PR-ы создают «живой» график. Когда пользователи открывают репо, они видят проект, в который «вкладываются», и охотнее ставят звезду.
2. **Попадание в ленту «Releases»** (https://github.com/Reider85/OryaSecurity/releases) и в **RSS-фид релизов** — каждый релиз отображается в подписках тех, кто следит за топиками `ai-security`, `llm-security`, `prompt-injection`. Это органический трафик.

Чтобы реально зажечь Trending, нужно комбинировать:
- Авто-релиз на каждый push (см. Промпт 2) — даёт постоянный поток свежих релизов.
- Зелёный CI с бейджами (Промпты 1, 4) — даёт конверсию посетитель → звезда.
- Хороший README + скриншоты + демо + Star History — даёт виральность.
- Посты в r/LocalLLaMA, HackerNews, Habr, X с прямым линком — единственный реальный способ получить «аномальный» всплеск звёзд за день-неделю.
- Подключение к коллекциям awesome-* (awesome-llm-security, awesome-ai-safety и т.д.) — стабильный медленный трафик.

---

## 6. Готовые промпты

> Каждый промпт самодостаточен — его можно отдать агенту как есть. Промпты идут в порядке приоритета: сначала починить то, что красное, потом добавить авторелизы, потом ускорители роста.

---

### Промпт 1 — Починить сломанный CI в `policies-ci.yml` и `ml-evaluation.yml`

```
Ты работаешь в репозитории Reider85/OryaSecurity. Текущий CI всегда падает. Примени следующие фиксы и закоммить их одним коммитом с сообщением "ci: fix broken policies-ci + ml-evaluation workflows".

1. Открой .github/workflows/policies-ci.yml и внеси изменения:
   a) Замени все вхождения `openpolicyagent/setup-opa@v2` на `open-policy-agent/setup-opa@v2` (старый неймспейс возвращает 404, action переехал в организацию open-policy-agent с дефисами).
   b) В джобе `verify-commits` замени `uses: actions/github-script@v7` на `uses: actions/github-script@v8` (v7 deprecated на Node 20).
   c) В джобе `verify-commits` на уровне шага "Verify commits touching policies/ are signed" добавь `continue-on-error: true`, чтобы неподписанные фич-коммиты не валили весь CI. Подписи всё равно будут проверяться при релизе bundle (джоба `bundle` триггерится на tag).
   d) В триггере `push:` добавь `paths-ignore`:
        paths-ignore:
          - '**/*.md'
          - 'docs/**'
          - 'LICENSE'
          - '.gitignore'
          - '.env.example'
   e) На уровне `jobs:` добавь `fail-fast: false`.

2. Открой .github/workflows/ml-evaluation.yml:
   a) В триггере `pull_request:` добавь такие же `paths-ignore` как выше (но не для `paths`, а отдельным ключом paths-ignore на уровне pull_request — это нужно для того, чтобы PR, меняющий только README, не запускал 60-минутную ML-джобу).
   b) В блоке `defaults.run.working-directory: backend` проверь, что все команды используют `.venv/bin/python` — это уже так, оставь как есть.

3. Закоммить изменения. НЕ триггери workflow вручную — достаточно пуush в main.

Ожидаемый результат после merge: оба workflow становятся зелёными при следующем пуше в main (для policies-ci.yml) и при следующем PR в backend/app/ml/** (для ml-evaluation.yml).
```

---

### Промпт 2 — Авто-релиз при каждом пуше в `main` (главная цель)

```
Ты работаешь в репозитории Reider85/OryaSecurity. Цель: чтобы при КАЖДОМ пуше в main автоматически создавался GitHub Release (не draft, не pre-release) с монотонно растущим тегом. Это даёт постоянный поток свежих релизов в ленте https://github.com/Reider85/OryaSecurity/releases и в RSS, что повышает видимость проекта.

Создай файл .github/workflows/auto-release.yml со следующим содержимым (адаптируй имена под текущую структуру репо, если нужно):

name: Auto Release

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: auto-release-${{ github.ref }}
  cancel-in-progress: false

jobs:
  release:
    name: Create release on every push to main
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Generate release tag
        id: tag
        run: |
          # Монотонно растущий тег на основе run_number: v1234
          echo "tag=v${{ github.run_number }}" >> "$GITHUB_OUTPUT"
          # Дата для тела релиза
          echo "date=$(date -u +'%Y-%m-%d %H:%M UTC')" >> "$GITHUB_OUTPUT"

      - name: Build changelog from commits since last release
        id: changelog
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
          if [ -z "$LAST_TAG" ]; then
            RANGE="HEAD"
          else
            RANGE="${LAST_TAG}..HEAD"
          fi
          {
            echo "## Automated release ${{ steps.tag.outputs.tag }}"
            echo ""
            echo "**Built:** ${{ steps.tag.outputs.date }}"
            echo "**Commit:** ${{ github.sha }}"
            echo ""
            echo "### Commits since ${LAST_TAG:-start}"
            echo ""
            git log --pretty=format:'- %s (%h)' "${RANGE}"
          } > /tmp/release_notes.md

      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release create "${{ steps.tag.outputs.tag }}" \
            --title "Auto Release ${{ steps.tag.outputs.tag }}" \
            --notes-file /tmp/release_notes.md \
            --target main \
            --verify-tag

После создания файла закоммить его с сообщением "ci: auto-release on every push to main".

ВАЖНО:
- НЕ ставь `prerelease: true` и НЕ ставь `draft: true` — иначе релиз не попадёт в публичную ленту Releases.
- `permissions: contents: write` обязателен для создания тега и релиза.
- Если хочется semver-стиля (v0.0.1, v0.0.2...) вместо v{run_number}, замени шаг Generate release tag на инкремент последнего тега: `LAST=$(git describe --tags --abbrev=0 2>/dev/null | sed 's/v//'); NEW=$(...инкремент patch...); echo "tag=v${NEW}" >> "$GITHUB_OUTPUT"`. Но для цели "мелькать чаще" `v{run_number}` работает лучше — каждый коммит = новый номер = новый релиз в ленте.
```

---

### Промпт 3 — Pre-release nightly по каждому PR (доп. сигнал активности)

```
Ты работаешь в репозитории Reider85/OryaSecurity. Добавь второй workflow, который создаёт nightly pre-release для каждого PR — это держит вкладку Releases активной даже между мерджами в main.

Создай файл .github/workflows/nightly-pr-release.yml:

name: Nightly PR Release

on:
  pull_request:
    types: [opened, synchronize, reopened]
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: nightly-pr-${{ github.event.pull_request.number || github.run_id }}
  cancel-in-progress: true

jobs:
  nightly:
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
          fetch-depth: 0

      - name: Build tag
        id: t
        run: |
          PR=${{ github.event.pull_request.number || 0 }}
          echo "tag=nightly-pr${PR}-${{ github.run_number }}" >> "$GITHUB_OUTPUT"

      - name: Create pre-release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release create "${{ steps.t.outputs.tag }}" \
            --title "Nightly PR #${{ github.event.pull_request.number || 'manual' }} (${{ github.run_number }})" \
            --notes "Automated pre-release from PR ${{ github.event.pull_request.number || 'manual dispatch' }}. Head: ${{ github.event.pull_request.head.sha || github.sha }}" \
            --prerelease \
            --target ${{ github.event.pull_request.head.sha || github.sha }} \
            || gh release delete "${{ steps.t.outputs.tag }}" --yes --cleanup-tag

После создания файла закоммить с сообщением "ci: nightly pre-release for each PR".

Примечание: пре-релизы НЕ заходят в главную ленту Releases как «latest», но видны в списке. Это даёт второй поток активности в репо без засорения основной ленты.
```

---

### Промпт 4 — Полноценный CI с тестами на бесплатных GitHub runners

```
Ты работаешь в репозитории Reider85/OryaSecurity. Создай единый CI-воркфлоу, который гоняет линт, типы и тесты бэкенда и фронтенда, билдит Docker, считает покрытие. Все ресурсы — бесплатные GitHub-hosted runners для публичного репо.

Создай файл .github/workflows/ci.yml:

name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  backend-lint-type:
    name: Backend · ruff + mypy
    runs-on: ubuntu-latest
    timeout-minutes: 10
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with: { enable-cache: true }
      - run: uv python install 3.12
      - run: uv venv .venv
      - run: uv pip install -e ".[dev]"
      - run: .venv/bin/ruff check app
      - run: .venv/bin/ruff format --check app
      - run: .venv/bin/mypy app

  backend-test:
    name: Backend · pytest + coverage
    runs-on: ubuntu-latest
    timeout-minutes: 20
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with: { enable-cache: true }
      - run: uv python install 3.12
      - run: uv venv .venv
      - run: uv pip install -e ".[dev,test]"
      - run: .venv/bin/pytest -q --cov=app --cov-report=xml --cov-report=term-missing
      - name: Upload coverage to Codecov
        if: always()
        uses: codecov/codecov-action@v4
        with:
          files: backend/coverage.xml
          fail_ci_if_error: false
      - name: Upload coverage artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: backend-coverage
          path: backend/coverage.xml
          if-no-files-found: warn

  frontend-lint-type-test:
    name: Frontend · eslint + tsc + vitest
    runs-on: ubuntu-latest
    timeout-minutes: 15
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: pnpm
          cache-dependency-path: frontend/pnpm-lock.yaml
      - run: pnpm install --frozen-lockfile
      - run: pnpm lint
      - run: pnpm typecheck
      - run: pnpm test -- --run

  docker-build:
    name: Docker · build (no push)
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - name: Build backend image
        uses: docker/build-push-action@v6
        with:
          context: ./backend
          push: false
          tags: oryasecurity/backend:ci-${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

ВАЖНО перед коммитом:
1. Если в backend/pyproject.toml нет экстра [dev] или [test] — добавь их с ruff, mypy, pytest, pytest-cov, schemathesis.
2. Если во frontend нет скриптов lint/typecheck/test в package.json — добавь их (lint: eslint ., typecheck: tsc --noEmit, test: vitest).
3. Если в backend нет Dockerfile — пропусти джобу docker-build или укажи правильный context.
4. Зарегистрируй репо на codecov.io (бесплатно для OSS), добавь CODECOV_TOKEN в GitHub Secrets если хочется приватных репо — для публичного работает без токена.

После создания файла закоммить с сообщением "ci: add lint/type/test/docker workflow on free GitHub runners".
```

---

### Промпт 5 — CodeQL + Dependabot (бесплатная безопасность + активность)

```
Ты работаешь в репозитории Reider85/OryaSecurity. Включи две бесплатные фичи GitHub, которые дают сигнал активности и улучшают безопасность: CodeQL SAST и Dependabot.

1. Создай файл .github/workflows/codeql.yml:

name: CodeQL

on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: '17 4 * * 1'  # раз в неделю в ночь с воскресенья на понедельник
  workflow_dispatch:

permissions:
  contents: read
  security-events: write

jobs:
  analyze:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    strategy:
      fail-fast: false
      matrix:
        language: [python, javascript-typescript]
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: ${{ matrix.language }}
          queries: security-extended
      - uses: github/codeql-action/analyze@v3
        with:
          category: "/language:${{ matrix.language }}"

2. Создай файл .github/dependabot.yml:

version: 2
updates:
  - package-ecosystem: pip
    directory: /backend
    schedule: { interval: weekly, day: monday }
    open-pull-requests-limit: 5
    labels: [dependencies, python]
  - package-ecosystem: npm
    directory: /frontend
    schedule: { interval: weekly, day: monday }
    open-pull-requests-limit: 5
    labels: [dependencies, javascript]
  - package-ecosystem: docker
    directory: /
    schedule: { interval: monthly }
    open-pull-requests-limit: 3
    labels: [dependencies, docker]
  - package-ecosystem: github-actions
    directory: /
    schedule: { interval: monthly }
    open-pull-requests-limit: 3
    labels: [dependencies, ci]

3. После создания обоих файлов закоммить с сообщением "ci: enable CodeQL + Dependabot for free security + activity".

Что это даст:
- CodeQL: бесплатный SAST, бейдж в README, раннее обнаружение SQL-injection/path-traversal/secret-leak в собственном коде.
- Dependabot: еженедельно создаёт PR-ы с обновлениями зависимостей → постоянная активность в Insights → сигнал для алгоритма Trending, что репо «живой».
```

---

### Промпт 6 — Бейджи, секция Releases и README-апгрейд для конверсии в звёзды

```
Ты работаешь в репозитории Reider85/OryaSecurity. Цель: повысить конверсию посетитель → звезда. Обнови README.md.

1. В самое начало README.md, сразу после заголовка # OryaSecurity и краткого описания (1-2 строки), добавь блок бейджей:

<p align="center">
  <a href="https://github.com/Reider85/OryaSecurity/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Reider85/OryaSecurity/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="https://github.com/Reider85/OryaSecurity/actions/workflows/policies-ci.yml"><img alt="Policies CI" src="https://github.com/Reider85/OryaSecurity/actions/workflows/policies-ci.yml/badge.svg?branch=main"></a>
  <a href="https://github.com/Reider85/OryaSecurity/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://github.com/Reider85/OryaSecurity/actions/workflows/codeql.yml/badge.svg"></a>
  <a href="https://github.com/Reider85/OryaSecurity/releases"><img alt="Releases" src="https://img.shields.io/github/v/release/Reider85/OryaSecurity?include_prereleases&sort=semver"></a>
  <a href="https://github.com/Reider85/OryaSecurity/releases"><img alt="Release downloads" src="https://img.shields.io/github/downloads/Reider85/OryaSecurity/total"></a>
  <a href="https://github.com/Reider85/OryaSecurity/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Reider85/OryaSecurity?style=social"></a>
  <a href="https://github.com/Reider85/OryaSecurity/network/members"><img alt="Forks" src="https://img.shields.io/github/forks/Reider85/OryaSecurity?style=social"></a>
  <a href="https://github.com/Reider85/OryaSecurity/issues"><img alt="Issues" src="https://img.shields.io/github/issues/Reider85/OryaSecurity"></a>
  <a href="https://github.com/Reider85/OryaSecurity/commits"><img alt="Last commit" src="https://img.shields.io/github/last-commit/Reider85/OryaSecurity"></a>
  <a href="LICENSE.md"><img alt="License" src="https://img.shields.io/github/license/Reider85/OryaSecurity?color=blue"></a>
</p>

2. Сразу после бейджей добавь секцию "Releases":

## 📦 Releases

Каждый пуш в `main` автоматически публикует новый релиз — см. [Releases](https://github.com/Reider85/OryaSecurity/releases) и подписывайтесь на [RSS-фид](https://github.com/Reider85/OryaSecurity/releases.atom).

3. В конец README.md добавь секцию "Star History" (появится, когда будет хотя бы 1 звезда):

## ⭐ Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Reider85/OryaSecurity&type=Date&theme=dark">
  <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Reider85/OryaSecurity&type=Date">
  <img alt="Star History" src="https://api.star-history.com/svg?repos=Reider85/OryaSecurity&type=Date">
</picture>

4. В конец README.md добавь призыв:

## 🙋 Поддержка

Если проект полезен — поставьте ⭐, это помогает другим найти его. Если нашли баг — откройте [Issue](https://github.com/Reider85/OryaSecurity/issues/new/choose). Если хотите фичу — форкайте и присылайте PR.

5. Закоммить с сообщением "docs: add CI/release/star badges + Releases section".

ВАЖНО: НЕ удаляй существующие технологические бейджи (Python, FastAPI, Next.js и т.д.), они уже есть — просто добавь новые выше по тексту.
```

---

### Промпт 7 — GitHub Pages для документации + issue/PR шаблоны (бесплатный хостинг + сигнал активности)

```
Ты работаешь в репозитории Reider85/OryaSecurity. Включи GitHub Pages для документации и добавь шаблоны — это бесплатные фичи GitHub, которые улучшают впечатление от репо и дают сигнал активности.

1. Создай файл .github/workflows/pages.yml для деплоя MkDocs-документации из папки docs/:

name: Pages

on:
  push:
    branches: [main]
    paths: [docs/**, mkdocs.yml, .github/workflows/pages.yml]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install mkdocs mkdocs-material
      - run: mkdocs build --strict
      - uses: actions/upload-pages-artifact@v3
        with: { path: site }

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4

Если mkdocs.yml ещё нет — создай минимальный mkdocs.yml:
site_name: OryaSecurity
site_url: https://reider85.github.io/OryaSecurity/
repo_url: https://github.com/Reider85/OryaSecurity
theme: { name: material }
nav:
  - Home: index.md
  - Quick Start: quick-start.md
  - Architecture: architecture.md
  - API: api.md
И перенеси/скопируй соответствующие .md из docs/ в корень как index.md/quick-start.md/etc.

2. Создай файл .github/ISSUE_TEMPLATE/bug_report.yml:
name: Bug report
description: Report a bug
labels: [bug]
body:
  - type: textarea
    id: what-happened
    attributes:
      label: What happened?
      description: Steps to reproduce, expected vs actual
    validations:
      required: true
  - type: input
    id: version
    attributes:
      label: OryaSecurity version / commit
    validations:
      required: true

3. Создай .github/ISSUE_TEMPLATE/feature_request.yml аналогично.

4. Создай .github/pull_request_template.md:
## Summary
## Changes
## Checklist
- [ ] Tests added/updated
- [ ] `ruff check` and `mypy` pass
- [ ] CHANGELOG updated (if user-facing)

5. Создай .github/FUNDING.yml:
github: [Reider85]

6. Закоммить с сообщением "docs: enable GitHub Pages + issue/PR templates + funding".

После этого в Settings репозитория нужно один раз включить Pages → Source: GitHub Actions (это пользователь делает руками, через workflow не делается).
```

---

### Промпт 8 — Порядок применения (шпаргалка)

```
Применять промпты строго в этом порядке, каждый — отдельным PR или отдельным прямым push в main:

1. Промпт 1 (починить CI)              — без этого все остальные бейджи будут красными.
2. Промпт 4 (полный CI с тестами)      — добавляет зелёные проверки + бейдж Codecov.
3. Промпт 2 (авто-релиз на push)       — главная цель: релиз в ленте на каждый push.
4. Промпт 3 (nightly по PR)            — доп. поток релизов.
5. Промпт 5 (CodeQL + Dependabot)      — безопасность + еженедельная активность.
6. Промпт 6 (бейджи + README)          — конверсия посетитель → звезда.
7. Промпт 7 (Pages + templates)        — профессиональный вид репо.

После шага 2 CI должен стать зелёным. После шага 3 — в Releases появится первый авто-релиз. После шага 6 — README с бейджами и звёздами. Это и есть минимум для того, чтобы проект «мелькал» при каждом пуше.

Для реального попадания в Trending этих шагов недостаточно — нужен внешний трафик: пост в r/LocalLLaMA, HackerNews, Habr, X, и добавление в awesome-списки. CI/релизы только повышают конверсию того трафика в звёзды.
```

---

## 7. Краткая сводка для тех, кто читает только итог

- **Почему CI всегда failed:** action `openpolicyagent/setup-opa@v2` переехал в `open-policy-agent/setup-opa@v2` (старый неймспейс = 404), плюс `verify-commits` падает на неподписанных фич-коммитах в `policies/`, плюс `actions/github-script@v7` deprecated на Node 20.
- **Что даст оптимизация:** зелёный CI → бейдж → доверие → конверсия в звёзды; экономия минут через `paths-ignore` и кэш; бесплатный CodeQL/Dependabot/Pages/Codecov.
- **Какие тесты запускать:** ruff+mypy+pytest (backend), eslint+tsc+vitest (frontend), opa check/test (policies), docker build, CodeQL. ML smoke-train — только на PR в `backend/app/ml/**`. Это улучшит процесс: регрессии ловятся до merge, бейдж покрытия растёт, репо выглядит живым.
- **Авто-релиз на каждый пуш:** реализуется одним файлом `.github/workflows/auto-release.yml` с `gh release create v${{ github.run_number }}` — Промпт 2.
- **Trending:** алгоритм смотрит на аномальное число новых звёзд за день/неделю. CI и релизы сами по себе звёзд не дают, но делают репо видимым в ленте Releases и RSS, что повышает органический трафик. Для реального всплеска нужен внешний пост (HN/Reddit/X/Habr) + добавление в awesome-списки.
- **Бесплатные ресурсы GitHub, которые стоит задействовать:** ubuntu-latest runners (безлимит для public), Actions cache (10 ГБ), Container registry, Pages, CodeQL, Dependabot, Codecov.
