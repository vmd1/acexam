-- Phase 8 Postgres Schema

-- One canonical row per (exam_board, level, subject) qualification, since
-- no such table exists today (spec_topics is per-topic, user_subjects is
-- per-student) -- gives qualification-level facts like tiers a home.
CREATE TABLE IF NOT EXISTS qualifications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_board  TEXT NOT NULL,
    level       TEXT NOT NULL,
    subject     TEXT NOT NULL,
    tiers       TEXT[] NOT NULL DEFAULT '{}',   -- e.g. '{Higher,Foundation}', empty if untiered
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_qualifications ON qualifications (exam_board, level, subject);

ALTER TABLE papers ADD COLUMN IF NOT EXISTS level TEXT NOT NULL DEFAULT 'GCSE';
ALTER TABLE papers ADD COLUMN IF NOT EXISTS tier TEXT;
ALTER TABLE user_subjects ADD COLUMN IF NOT EXISTS tier TEXT;

INSERT INTO qualifications (exam_board, level, subject, tiers)
VALUES ('AQA', 'GCSE', 'Biology', '{Higher}')
ON CONFLICT (exam_board, level, subject) DO UPDATE SET tiers = EXCLUDED.tiers;
