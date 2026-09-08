-- Phase 14 Postgres Schema

-- Ingestion's own token_usage (§5 cost model) was only ever returned in the
-- POST /upload response body and discarded once that request finished -
-- nothing persisted it, so there was no way to see a paper's ingestion
-- cost later from the admin review page. Stored as JSONB matching
-- ai_pipeline.get_token_usage()'s shape: {prompt_tokens, output_tokens,
-- thoughts_tokens, total_tokens, call_count}.
ALTER TABLE papers ADD COLUMN IF NOT EXISTS ingestion_token_usage JSONB;
