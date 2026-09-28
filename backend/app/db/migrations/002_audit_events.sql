-- Audit log: one row per scan decision
CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id UUID NOT NULL,
    tenant_id VARCHAR(64),
    prompt_hash VARCHAR(64) NOT NULL,
    prompt_text_redacted TEXT,
    verdict VARCHAR(16) NOT NULL,
    reason TEXT,
    rules_matched JSONB NOT NULL DEFAULT '[]'::jsonb,
    policy_version VARCHAR(16),
    latency_ms REAL,
    CONSTRAINT ck_audit_events_verdict CHECK (verdict IN ('allow', 'block'))
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS ix_audit_events_tenant_ts ON audit_events(tenant_id, ts DESC);
CREATE INDEX IF NOT EXISTS ix_audit_events_prompt_hash ON audit_events(prompt_hash);
CREATE INDEX IF NOT EXISTS ix_audit_events_verdict ON audit_events(verdict);
