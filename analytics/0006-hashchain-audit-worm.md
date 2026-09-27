# ADR-0006: Hashchain audit + WORM-хранилище (не blockchain)

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-27 |
| **Deciders** | Architecture Team, Compliance, SecEng |
| **Related** | ADR-0004, ADR-0011, ADR-0014 |

## Context

Сканер принимает решения о блокировке/пропуске запросов. Для regulated industries (финансы, здравоохранение, гос.) необходимо доказать регулятору, что политика применялась. Это требует:

1. **Tamper-evident** — любое изменение/удаление записи должно быть обнаружимо.
2. **Append-only** — нельзя перезаписать существующую запись.
3. **Verifiable integrity** — независимая проверка без доверия к сканеру.
4. **Long retention** — 1–7 лет горячих/холодных данных.
5. **Compliance-ready export** — выгрузка в SIEM (Splunk, QRadar) для external audit.

В исходной архитектуре презентации audit не описан явно.

### Forces

- **Cost**: полный blockchain (Ethereum, Hyperledger) — overkill для аудита одного сервиса.
- **Latency**: синхронная запись в audit не должна добавлять > 5 ms к запросу.
- **Verifiability**: третья сторона (regulator) должна уметь проверить без нашего ПО.
- **Tamper-evidence**: даже root-пользователь сканера не должен уметь подменить историю незаметно.
- **Volume**: 1M audit events/day × 1KB = 1 GB/day = 365 GB/year hot storage.
- **Retention**: 1 год hot + 7 лет cold (для financial compliance).

## Decision

**Принять hashchain-схему поверх WORM-хранилища (S3 Object Lock или immudb).**

### Архитектура

```
[Scanner] ──> [Audit Event] ──> [Hashchain Engine] ──> [WORM Storage]
                                              │
                                              └──> [Notary snapshot (external, daily)]
```

### Схема hashchain

Каждый audit event содержит хеш предыдущего:

```python
def write_audit(event: AuditEvent):
    prev_hash = audit_store.get_last_hash()
    event_dict = {
        "ts": event.ts.isoformat(),
        "tenant_id": event.tenant_id,
        "request_id": event.request_id,
        "prompt_hash": sha256(event.prompt.encode()).hexdigest(),
        "verdict": event.verdict,  # allow/block/redact/...
        "policy_version": event.policy_version,
        "detectors": event.detector_scores,
        "prev_hash": prev_hash,
    }
    event_dict["hash"] = sha256(
        canonical_json(event_dict).encode()  # canonical: sorted keys
    ).hexdigest()
    audit_store.append(event_dict)  # WORM storage — append only
    return event_dict["hash"]
```

### WORM storage options

| Опция | Описание | Когда выбирать |
|---|---|---|
| **S3 + Object Lock (COMPLIANCE mode)** | Cloud-native WORM, retention policy enforced at storage level | AWS-деплой |
| **MinIO + Object Lock** | Self-hosted S3-compatible WORM | On-prem |
| **immudb** | Embedded tamper-evident DB, built-in hashchain | Single-node deployments |
| **Append-only PostgreSQL table + REVOKE UPDATE** | Lightweight, но не настоящая WORM (root может GRANT обратно) | Только для dev |

**Выбор:** MinIO + Object Lock для on-prem primary, immudb как embedded fallback для лёгких деплоев.

### Notary snapshots (дополнительная защита)

Ежедневно внешний notary-сервис (отдельный от сканера, минимальный trust surface) делает snapshot последнего hash'а:

```
2026-09-27 23:59 UTC: last_hash=abc123...  signed by notary private key
2026-09-28 23:59 UTC: last_hash=def456...  signed by notary private key
```

Подпись notary публикуется в публичном месте (например, Git-репозиторий, GitHub Pages). Это даёт дополнительную защиту: даже если атакующий получит root на сканере, он не сможет подделать подпись notary.

### Verification flow (для compliance audit)

```
1. Регулятор запрашивает audit trail за период [T1, T2]
2. Сканер выгружает events [T1, T2] + хеш первого и последнего
3. Регулятор:
   a. Проверяет каждый event: prev_hash соответствует hash предыдущего
   b. Сверяет last_hash с подписью notary
   c. Если всё совпадает → integrity доказана
4. Отчёт экспортируется в PDF/JSON для compliance archive
```

## Consequences

### Positive

- ✅ Tamper-evident: подмена любой записи нарушает цепочку хешей.
- ✅ Append-only: WORM storage физически не позволяет перезапись.
- ✅ Verifiable третьей стороной без доверия к сканеру.
- ✅ Notary snapshot защищает от root-компрометации.
- ✅ S3 Object Lock / MinIO — стандартные технологии, easy to operate.
- ✅ Compliance export в SIEM для SOC-команд.

### Negative

- ❌ Hashchain ломается при конкурирующих записях (race condition). Mitigation: serialization через single-writer или lock-based append.
- ❌ Если первый event подменён — вся последующая цепочка валидна. Mitigation: notary snapshot — reset точки доверия.
- ❌ Storage cost: WORM нельзя компрессировать post-factum (нельзя переписать). Mitigation: gzip-сжатие до записи.
- ❌ PII в audit log — нужно redact перед записью (через Vault tokens, см. ADR-0007).

### Neutral

- ➖ Canonical JSON (sorted keys) обязателен — иначе хеши неконсистентны между реализациями.
- ➖ Storage layout: год/месяц/день buckets для удобства retention и query.

## Alternatives Considered

### Alternative A: Blockchain (Hyperledger Fabric, Ethereum private)

| Аспект | Оценка |
|---|---|
| Pros | Максимальная tamper-resistance, decentralised trust |
| Cons | Latency 100 ms+ на блок, сложная инфраструктура (consensus nodes), overkill для single-org аудита |
| Why rejected | NRE cost и operational complexity несопоставимы с выгодой. Блокчейн оправдан при отсутствии единого доверенного оператора, у нас же сканер — единый |

**Вердикт:** Отклонено.

### Alternative B: Plain PostgreSQL с trigger-based audit

| Аспект | Оценка |
|---|---|
| Pros | Простота, ACID, SQL queryability |
| Cons | Root PostgreSQL может `UPDATE`/`DELETE` любые записи. Не tamper-evident |
| Why rejected | Не проходит compliance audit |

**Вердикт:** Отклонено.

### Alternative C: Merkle Tree periodic snapshot

Дерево Меркла, периодически snapshot'ся в external notary.

| Аспект | Оценка |
|---|---|
| Pros | Эффективная проверка больших объёмов (log N вместо N) |
| Cons | Сложнее реализация, нужно хранить proofs для каждой проверки |
| Why rejected | Hashchain проще, а overhead на проверку приемлем для наших объёмов (1M events/day) |

**Вердикт:** Возможно в Phase 3 для больших объёмов.

### Alternative D: Cloud-native audit services (AWS CloudTrail, GCP Audit Logs)

| Аспект | Оценка |
|---|---|
| Pros | Zero-ops, native integration |
| Cons | Vendor lock-in, не работает on-prem, limited schema customization |
| Why rejected | Противоречит требованию локального развёртывания |

**Вердикт:** Возможно для SaaS-режима Enterprise.

## Related Decisions

- **ADR-0004** (OPA PDP) — audit log содержит policy_version.
- **ADR-0007** (Vault) — PII в audit замаскирован tokens.
- **ADR-0011** (Multi-tenancy) — per-tenant audit buckets с разными KMS-ключами.
- **ADR-0014** (On-prem Deployment) — MinIO как WORM storage.

## References

- [S3 Object Lock documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)
- [immudb: tamper-evident DB](https://docs.immudb.io/)
- [MinIO Object Lock](https://min.io/docs/minio/linux/administration/object-retention.html)
- [WORM storage compliance](https://csrc.nist.gov/glossary/term/worm) — NIST glossary
- ARCHITECT.md, раздел 6.8 — hashchain audit pseudocode
