-- Phase 11 Postgres Schema

-- The §6.2 independent auto-grader now produces a full structured label
-- (feedback_text, missed_points, misconception_tags), not just a mark
-- count, matching the §6.5 training example format -- carry all of it so
-- later JSONL assembly doesn't need to re-derive it.
ALTER TABLE training_examples ADD COLUMN IF NOT EXISTS feedback_text TEXT;
ALTER TABLE training_examples ADD COLUMN IF NOT EXISTS missed_points JSONB NOT NULL DEFAULT '[]';
ALTER TABLE training_examples ADD COLUMN IF NOT EXISTS misconception_tags JSONB NOT NULL DEFAULT '[]';
