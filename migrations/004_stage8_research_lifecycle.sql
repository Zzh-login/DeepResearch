BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TEMP TABLE stage8_legacy_task_status
ON COMMIT DROP
AS
SELECT id, status AS previous_status
FROM research_tasks
WHERE status IN ('pending', 'running');

ALTER TABLE research_tasks
    ADD COLUMN IF NOT EXISTS state_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS resume_from TEXT,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS pause_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_event_sequence BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS lease_owner TEXT,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS research_events (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL
        REFERENCES research_tasks(id)
        ON DELETE CASCADE,
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_research_events_task_sequence
ON research_events(task_id, sequence);

CREATE TABLE IF NOT EXISTS research_checkpoints (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL
        REFERENCES research_tasks(id)
        ON DELETE CASCADE,
    node_name TEXT NOT NULL,
    state JSONB NOT NULL,
    state_hash TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_research_checkpoints_latest
ON research_checkpoints(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS research_artifacts (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL
        REFERENCES research_tasks(id)
        ON DELETE CASCADE,
    kind TEXT NOT NULL
        CHECK (kind IN ('markdown', 'docx', 'pdf')),
    filename TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, kind, sha256)
);

CREATE INDEX IF NOT EXISTS idx_research_artifacts_task
ON research_artifacts(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS research_usage (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL
        REFERENCES research_tasks(id)
        ON DELETE CASCADE,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    estimated_cost NUMERIC(14, 8),
    usage_estimated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_research_usage_task
ON research_usage(task_id, created_at);

CREATE TABLE IF NOT EXISTS audit_events (
    id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    conversation_id UUID,
    research_task_id UUID,
    mode TEXT,
    event_type TEXT NOT NULL,
    result_code TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_events_owner_created
ON audit_events(owner_id, created_at DESC);

DO $$
DECLARE
    item RECORD;
BEGIN
    FOR item IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'research_tasks'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%status%'
    LOOP
        EXECUTE format(
            'ALTER TABLE research_tasks DROP CONSTRAINT %I',
            item.conname
        );
    END LOOP;
END $$;

UPDATE research_tasks
SET
    status = 'created',
    current_step = CASE
        WHEN current_step IS NULL
             OR current_step = ''
             OR current_step IN ('等待处理', '研究中')
        THEN '等待恢复（旧任务）'
        ELSE current_step
    END,
    next_attempt_at = COALESCE(next_attempt_at, NOW()),
    updated_at = NOW()
WHERE status IN (
    'pending',
    'running'
);

ALTER TABLE research_tasks
    ALTER COLUMN status SET DEFAULT 'created';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'research_tasks_status_stage8_check'
          AND conrelid = 'research_tasks'::regclass
    ) THEN
        ALTER TABLE research_tasks
            ADD CONSTRAINT research_tasks_status_stage8_check
            CHECK (
                status IN (
                    'created',
                    'planning',
                    'searching',
                    'reading',
                    'verifying',
                    'writing',
                    'paused',
                    'completed',
                    'failed',
                    'cancelled'
                )
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_research_tasks_claim_stage8
ON research_tasks(status, next_attempt_at, created_at);

DROP INDEX IF EXISTS idx_research_tasks_queue;

INSERT INTO research_events (
    id,
    task_id,
    sequence,
    event_type,
    status,
    payload
)
SELECT
    gen_random_uuid(),
    legacy.id,
    1,
    'task.migrated',
    'created',
    jsonb_build_object(
        'reason', 'stage8 migration',
        'previous_status', legacy.previous_status
    )
FROM stage8_legacy_task_status AS legacy
WHERE NOT EXISTS (
    SELECT 1
    FROM research_events AS existing
    WHERE existing.task_id = legacy.id
);

INSERT INTO research_events (
    id,
    task_id,
    sequence,
    event_type,
    status,
    payload
)
SELECT
    gen_random_uuid(),
    task.id,
    1,
    'task.created',
    task.status,
    jsonb_build_object(
        'reason', 'backfill missing initial event'
    )
FROM research_tasks AS task
WHERE task.last_event_sequence = 0
  AND NOT EXISTS (
      SELECT 1
      FROM research_events AS existing
      WHERE existing.task_id = task.id
  );

UPDATE research_tasks AS task
SET last_event_sequence = COALESCE(
    (
        SELECT MAX(event.sequence)
        FROM research_events AS event
        WHERE event.task_id = task.id
    ),
    0
);

INSERT INTO schema_migrations (version)
VALUES ('004_stage8_research_lifecycle')
ON CONFLICT (version) DO NOTHING;

COMMIT;