CREATE TABLE IF NOT EXISTS research_tasks (
    id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    knowledge_base_id UUID NULL
        REFERENCES knowledge_bases(id) ON DELETE SET NULL,
    query TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending', 'running', 'completed', 'failed', 'cancelled'
        )),
    progress INTEGER NOT NULL DEFAULT 0
        CHECK (progress BETWEEN 0 AND 100),
    current_step TEXT NOT NULL DEFAULT '等待处理',
    report JSONB,
    error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_research_tasks_owner_created
ON research_tasks(owner_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_research_tasks_queue
ON research_tasks(status, created_at)
WHERE status IN ('pending', 'running');

CREATE TABLE IF NOT EXISTS research_sources (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL
        REFERENCES research_tasks(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL
        CHECK (source_type IN ('knowledge', 'web')),
    title TEXT NOT NULL,
    url TEXT,
    filename TEXT,
    excerpt TEXT NOT NULL,
    content TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, source_id),
    UNIQUE(task_id, url)
);

CREATE INDEX IF NOT EXISTS idx_research_sources_task
ON research_sources(task_id, source_id);