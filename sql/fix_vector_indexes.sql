-- ============================================================================
-- Fix vector-index recall.
-- ivfflat with lists=100 on a small table, under pgvector's default probes=1,
-- scans only ~1% of vectors, so top-k queries return too few rows (e.g. 2 for a
-- LIMIT 5). HNSW gives high recall at this scale without probe tuning, and still
-- scales to large tables. Run once (via the native role, e.g. the 00 notebook's
-- run_sql_file or a psycopg2 cell).
-- ============================================================================

DROP INDEX IF EXISTS idx_dest_desc_vec;
DROP INDEX IF EXISTS idx_act_req_vec;

CREATE INDEX IF NOT EXISTS idx_dest_desc_vec
    ON destinations USING hnsw (description_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_act_req_vec
    ON activities USING hnsw (requirements_embedding vector_cosine_ops);
