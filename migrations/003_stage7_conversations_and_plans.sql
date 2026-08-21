CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '新对话',
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_owner_updated
ON conversations(owner_id, archived, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    requested_mode TEXT NOT NULL CHECK (requested_mode IN (
        'normal', 'knowledge', 'hybrid', 'auto', 'deep_research'
    )),
    resolved_mode TEXT CHECK (resolved_mode IN (
        'normal', 'knowledge', 'hybrid', 'deep_research'
    )),
    knowledge_base_id UUID NULL
        REFERENCES knowledge_bases(id) ON DELETE SET NULL,
    research_task_id UUID NULL
        REFERENCES research_tasks(id) ON DELETE SET NULL,
    route_metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation_created
ON chat_messages(conversation_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_chat_messages_owner_created
ON chat_messages(owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS message_citations (
    id UUID PRIMARY KEY,
    message_id UUID NOT NULL
        REFERENCES chat_messages(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('knowledge', 'web')),
    source_id TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    UNIQUE(message_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_message_citations_message
ON message_citations(message_id, ordinal);

ALTER TABLE research_tasks
ADD COLUMN IF NOT EXISTS conversation_id UUID NULL
    REFERENCES conversations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_research_tasks_conversation
ON research_tasks(conversation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS research_plans (
    task_id UUID PRIMARY KEY
        REFERENCES research_tasks(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    raw_plan JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS research_subtasks (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL
        REFERENCES research_tasks(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 10),
    query TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    result_count INTEGER NOT NULL DEFAULT 0 CHECK (result_count >= 0),
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, ordinal),
    UNIQUE(task_id, query)
);

CREATE INDEX IF NOT EXISTS idx_research_subtasks_task
ON research_subtasks(task_id, ordinal);

CREATE TABLE IF NOT EXISTS research_request_keys (
    owner_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    task_id UUID NOT NULL
        REFERENCES research_tasks(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(owner_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_research_request_keys_expires
ON research_request_keys(expires_at);

CREATE TABLE IF NOT EXISTS service_heartbeats (
    service_name TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('starting', 'running', 'stopping')),
    metadata JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);