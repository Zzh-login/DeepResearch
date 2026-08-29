BEGIN;

CREATE TABLE IF NOT EXISTS research_export_jobs (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL
        REFERENCES research_tasks(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('markdown', 'docx', 'pdf')),
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'running', 'completed', 'failed')),
    artifact_id UUID NULL
        REFERENCES research_artifacts(id) ON DELETE SET NULL,
    error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_research_export_jobs_task_kind
        UNIQUE(task_id, kind)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'research_export_jobs'::regclass
          AND contype = 'u'
          AND pg_get_constraintdef(oid) = 'UNIQUE (task_id, kind)'
    ) THEN
        ALTER TABLE research_export_jobs
            ADD CONSTRAINT uq_research_export_jobs_task_kind
            UNIQUE (task_id, kind);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_research_export_jobs_claim
ON research_export_jobs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_research_export_jobs_owner
ON research_export_jobs(owner_id, created_at DESC);

INSERT INTO schema_migrations (version)
VALUES ('006_stage8_2_7_exports')
ON CONFLICT (version) DO NOTHING;

COMMIT;
