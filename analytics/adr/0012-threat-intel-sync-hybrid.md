# ADR-0012: Threat Intel Sync — hybrid (online + air-gapped bundles)

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-27 |
| **Deciders** | Architecture Team, SecEng |
| **Related** | ADR-0005, ADR-0014 |

## Context

База известных атак (prompt DB) статична и устаревает без обновлений. Prompt injection эволюционирует **ежедневно**:
- Новые джейлбрейки (DAN v15, «grandma exploit» variants, etc.),
- Новые adversarial patterns (GCG-атаки, multi-modal injection),
- Обновления OWASP LLM Top-10.

Без обновления threat-intel сканер устаревает за 1–2 недели. В исходной архитектуре презентации **нет механизма обновления** — критический провал.

### Forces

- **Air-gapped клиенты**: on-prem без интернет-доступа (банки, гос.) — не могут тянуть online feed.
- **Latency**: обновления не должны влиять на inline-проверки (async).
- **Trust**: внешний threat-intel может содержать вредоносные payloads (нужно подписывать).
- **Frequency**: online — каждые 15 минут, offline — каждые 1–7 дней.
- **Rollback**: если новое обновление вызвало рост FP — мгновенный откат.

## Decision

**Принять hybrid-схему: online pull + offline signed bundles + emergency push.**

### Три режима синхронизации

| Режим | Источник | Механизм | Latency | Когда |
|---|---|---|---|---|
| **Online** | Central threat-intel feed | Pull каждые 15 минут (signed JSON) | < 30 min | Default, online клиенты |
| **Offline (air-gapped)** | Signed bundle | SOC engineer загружает через USB / internal artifact repo | 1–7 days | Banks, gov, regulated |
| **Emergency** | SOC manual push | Admin API + 2FA approval | < 5 min | Critical 0-day attack |

### Online режим

```
[Central Threat Intel Service] ─signs─> [JSON Manifest]
                                              │
                                              └─> [Scanner pulls every 15 min]
                                                      │
                                                      └─> [Verify signature]
                                                              │
                                                              └─> [Update Qdrant: upsert vectors]
```

**Manifest format**:
```json
{
  "version": "2026-09-27T15:00Z",
  "signature": "BASE64_ED25519_SIG",
  "updates": [
    {
      "id": "attack-12345",
      "type": "prompt_injection",
      "text": "ignore previous instructions and ...",
      "embedding": [0.123, -0.456, ...],
      "metadata": {"severity": "high", "source": "advbench"}
    }
  ],
  "deletions": ["attack-12340"],
  "prev_version": "2026-09-27T14:45Z"
}
```

- Подпись Ed25519 приватным ключом central threat-intel service.
- Сканер верифицирует публичным ключом (зашит в конфиг).
- atomic update: либо все updates применяются, либо ни одного (транзакция).

### Offline режим (air-gapped)

```
[SOC engineer] ─downloads on internet-connected machine─> [Signed bundle .tar.gz]
                                                              │
                                                              └─> [Transfer via USB / signed artifact repo]
                                                                      │
                                                                      └─> [Scanner verifies signature]
                                                                              │
                                                                              └─> [Import to Qdrant]
```

- Bundle: tar.gz с JSON-манифестом + signature.
- Загрузка через USB или внутренний artifact-репозиторий (Nexus, Artifactory).
- SOC engineer'ы — ответственны за регулярность обновлений.
- Alerting: если bundle не импортирован > 7 дней — alert SecOps.

### Emergency push

- SOC发现 новый critical 0-day.
- Admin API: `POST /admin/threat-intel/emergency-import` с signed payload.
- 2FA required (TOTP + hardware key).
- Apply immediately (не ждём следующего pull cycle).
- Audit log: who pushed what when.

### Atomic update + rollback

Каждое обновление создаёт **новую версию** в Qdrant:
- Vector collection `prompt_attacks` → snapshot перед обновлением.
- Если что-то пошло не так → restore snapshot (single command, < 30 sec).
- Audit log: version_history с timestamps.

### Verification pipeline

```python
def apply_threat_intel_update(manifest: dict, signature: bytes) -> bool:
    # 1. Verify signature
    if not verify_ed25519(
        public_key=THREAT_INTEL_PUBKEY,
        message=canonical_json(manifest).encode(),
        signature=signature
    ):
        raise SecurityError("Invalid threat-intel signature")
    
    # 2. Snapshot Qdrant collection (for rollback)
    snapshot_id = qdrant.create_snapshot("prompt_attacks")
    
    # 3. Apply updates atomically
    try:
        with qdrant.batch():
            for upd in manifest["updates"]:
                qdrant.upsert("prompt_attacks", id=upd["id"], vector=upd["embedding"], payload=upd)
            for del_id in manifest["deletions"]:
                qdrant.delete("prompt_attacks", id=del_id)
        # 4. Invalidate decision cache (new attacks may match previously-cached prompts)
        redis.delete_pattern("decision_cache:*")
    except Exception as e:
        # Rollback on failure
        qdrant.restore_snapshot(snapshot_id)
        raise
    
    # 5. Audit
    audit.write({"event": "threat_intel_update", "version": manifest["version"], "snapshot_id": snapshot_id})
    return True
```

## Consequences

### Positive

- ✅ Online-клиенты получают обновления за < 30 минут.
- ✅ Air-gapped клиенты — через bundle, поддерживается.
- ✅ Emergency push для 0-day — < 5 минут.
- ✅ Signature verification — нет риска poisoning.
- ✅ Atomic updates + snapshots — мгновенный rollback.
- ✅ Cache invalidation — новые атаки не пропускаются из-за устаревших cache-вердиктов.

### Negative

- ❌ Online pull требует интернет-доступа сканера (для air-gapped — off).
- ❌ Signature key compromise — атакующий может подсунуть свои атаки. Mitigation: key rotation, multi-sig в Phase 3.
- ❌ Cache invalidation на каждое обновление — всплеск нагрузки на slow path. Mitigation: incremental invalidation (только prompts, matching новые attacks).
- ❌ SOC engineer забывает обновлять offline-клиентов — staleness alert нужен.

### Neutral

- ➖ Threat-intel manifest — открытый формат, можно интегрировать с другими вендорами.
- ➖ Public threat-intel shared across all tenants; private corpus — per-tenant.

## Alternatives Considered

### Alternative A: Online-only

| Аспект | Оценка |
|---|---|
| Pros | Простота, всегда свежие данные |
| Cons | Air-gapped клиенты не работают. Это блокирует значимый сегмент рынка (banking, gov) |
| Why rejected | Требование презентации — локальное развёртывание |

**Вердикт:** Отклонено.

### Alternative B: Manual CSV import

| Аспект | Оценка |
|---|---|
| Pros | Просто, нет automation needed |
| Cons | Human factor — забывают обновлять. Нет audit trail |
| Why rejected | Не масштабируется, не production-ready |

**Вердикт:** Отклонено.

### Alternative C: MISP-style feed integration

Использовать MISP (open-source threat-intel platform) для распространения.

| Аспект | Оценка |
|---|---|
| Pros | Стандартный формат, ecosystem, integration с SIEM |
| Cons | MISP — для традиционной cyber threat-intel (IPs, domains), не для prompt-injection corpus. Потребует custom galaxy |
| Why rejected | Overengineering для MVP. Возможно в Phase 3 для интеграции с SOC-инфраструктурой |

**Вердикт:** Возможно в Phase 3 как optional integration.

### Alternative D: Crowdsourced (tenant contributions)

Tenant'ы сами загружают новые атаки, видят их в общем corpus'е.

| Аспект | Оценка |
|---|---|
| Pros | Network effect, collective intelligence |
| Cons | Quality control, poisoning risk, privacy (tenant может загрузить чужой PII) |
| Why rejected | Требует moderation pipeline. Возможно в Phase 4 с reputation system |

**Вердикт:** Future direction, не MVP.

## Related Decisions

- **ADR-0005** (Qdrant) — Qdrant snapshots для atomic rollback.
- **ADR-0014** (On-prem Deployment) — offline bundles для air-gapped.

## References

- [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- [JailbreakBench](https://jailbreakbench.github.io/) — open corpus of jailbreaks
- [MISP project](https://www.misp-project.org/) — threat-intel platform
- [Ed25519 signatures](https://ed25519.cr.yp.to/)
- ARCHITECT.md, раздел 6.9 — threat intel sync modes
