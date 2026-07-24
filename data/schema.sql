-- ============================================================
-- pgvector 架构 DDL（与 infrastructure/memory/vector_repo.py 的
--   ensure_table() 完全一致，仅作为参考/手动初始化用）
-- 使用方式：psql -h localhost -U postgres -d robot -f schema.sql
-- 注：代码运行时已自动 CREATE TABLE，本文件主要用于审阅/手动建库
-- ============================================================

-- 1. 确保插件安装（pgvector 扩展）
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. 向量记忆表（语义检索）
--    注意：长期 KV 记忆（JsonLongMemory）是 JSON 文件存储，
--          不在此 DDL 中，请勿再建 memory_kv 表。
CREATE TABLE IF NOT EXISTS memory_vectors (
    id          SERIAL PRIMARY KEY,
    source      TEXT NOT NULL DEFAULT 'default',
    content_hash TEXT NOT NULL,
    summary     TEXT NOT NULL,
    full_text   TEXT DEFAULT '',
    embedding   vector(1024),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source, content_hash)
);

-- 3. HNSW 索引（pgvector 0.8.x 原生支持，余弦距离，无需训练）
CREATE INDEX IF NOT EXISTS idx_mv_embedding
    ON memory_vectors
    USING hnsw (embedding vector_cosine_ops);
