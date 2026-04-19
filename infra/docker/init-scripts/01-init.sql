-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 创建一个 health check 表
CREATE TABLE IF NOT EXISTS _health_check (
  id SERIAL PRIMARY KEY,
  checked_at TIMESTAMPTZ DEFAULT NOW()
);
