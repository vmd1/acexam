-- Phase 22 Postgres Schema

-- Grade boundaries were previously a single hardcoded GCSE 9-1 percentage
-- table in routers/analytics.py, applied identically to every subject/board/
-- level/tier. Real boundaries differ per qualification and per tier (e.g.
-- AQA GCSE Biology Higher vs Foundation), so they live here instead - one
-- row per (exam_board, level, subject, tier, grade), admin-configured via
-- PUT /admin/qualifications/{id}/grade-boundaries. tier follows the same
-- ''-for-untiered convention as spec_code_marking_models/training_jobs.
CREATE TABLE IF NOT EXISTS grade_boundaries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_board  TEXT NOT NULL,
    level       TEXT NOT NULL,
    subject     TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT '',
    grade       TEXT NOT NULL,          -- display label, e.g. '9', '8', 'A*'
    min_pct     NUMERIC NOT NULL,       -- minimum overall % to be predicted this grade
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_grade_boundaries_key
    ON grade_boundaries (exam_board, level, subject, tier, grade);
CREATE INDEX IF NOT EXISTS idx_grade_boundaries_lookup
    ON grade_boundaries (exam_board, level, subject, tier, min_pct DESC);
