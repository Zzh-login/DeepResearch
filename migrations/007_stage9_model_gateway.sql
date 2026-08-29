BEGIN;

CREATE TABLE IF NOT EXISTS model_usage_events (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    owner_id TEXT NOT NULL,
    conversation_id UUID,
    research_task_id UUID,
    mode TEXT NOT NULL,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('reserved', 'succeeded', 'failed')),
    reserved_input_tokens INTEGER NOT NULL DEFAULT 0
        CHECK (reserved_input_tokens >= 0),
    reserved_output_tokens INTEGER NOT NULL DEFAULT 0
        CHECK (reserved_output_tokens >= 0),
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    estimated_cost NUMERIC(14, 8),
    usage_estimated BOOLEAN NOT NULL DEFAULT FALSE,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    UNIQUE(request_id, attempt)
);

CREATE INDEX IF NOT EXISTS idx_model_usage_owner
ON model_usage_events(owner_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_usage_research
ON model_usage_events(research_task_id, created_at)
WHERE research_task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_model_usage_conversation
ON model_usage_events(conversation_id, created_at)
WHERE conversation_id IS NOT NULL;

ALTER TABLE audit_events
    ADD COLUMN IF NOT EXISTS request_id UUID,
    ADD COLUMN IF NOT EXISTS severity TEXT NOT NULL DEFAULT 'info';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'audit_events_severity_check'
          AND conrelid = 'audit_events'::regclass
    ) THEN
        ALTER TABLE audit_events
            ADD CONSTRAINT audit_events_severity_check
            CHECK (severity IN ('info', 'warning', 'error'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_audit_events_request
ON audit_events(request_id)
WHERE request_id IS NOT NULL;

INSERT INTO schema_migrations(version)
VALUES ('007_stage9_model_gateway')
ON CONFLICT (version) DO NOTHING;

COMMIT;