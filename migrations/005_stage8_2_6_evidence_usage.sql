BEGIN;

CREATE TABLE IF NOT EXISTS research_report_blocks (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    block_id TEXT NOT NULL,
    section TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    citation_ids JSONB NOT NULL DEFAULT '[]',
    based_on_block_ids JSONB NOT NULL DEFAULT '[]',
    columns JSONB NOT NULL DEFAULT '[]',
    rows JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, block_id),
    UNIQUE(task_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_research_report_blocks_task
ON research_report_blocks(task_id, ordinal);

CREATE TABLE IF NOT EXISTS research_evidence_snapshots (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('knowledge', 'web')),
    title TEXT NOT NULL,
    url TEXT,
    filename TEXT,
    excerpt TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    used_by_block_ids JSONB NOT NULL DEFAULT '[]',
    metadata JSONB NOT NULL DEFAULT '{}',
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_research_evidence_snapshots_task
ON research_evidence_snapshots(task_id, source_id);

INSERT INTO schema_migrations (version)
VALUES ('005_stage8_2_6_evidence_usage')
ON CONFLICT (version) DO NOTHING;

COMMIT;