-- SHAP explainability for classifier decisions (ALPHA Prompt 19)
--
-- init_db() replays every *.sql file on startup, so every statement here must
-- be idempotent. The severity column and the widened verdict CHECK only exist
-- in the Alembic chain (alembic/versions/002_post_factum_flag_severity.py);
-- adding them here keeps the raw-SQL path in sync, otherwise post_factum_flag
-- rows can never be written on databases initialised by init_db().

ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS severity VARCHAR(16);
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS explainability JSONB;

ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS ck_audit_events_verdict;
ALTER TABLE audit_events
    ADD CONSTRAINT ck_audit_events_verdict
    CHECK (verdict IN ('allow', 'block', 'post_factum_flag'));
