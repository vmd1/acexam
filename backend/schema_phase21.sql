-- Phase 21 Postgres Schema

-- Mirrors schema_phase20.sql's training_examples.www column but for live
-- marking: answers.missed_points already carries the model's ebi bullets,
-- but the www ("what went well") bullets were only ever folded into
-- feedback_text as markdown, never stored/returned as a structured list -
-- so the frontend couldn't render per-point ticks, only a text blob.
ALTER TABLE answers ADD COLUMN IF NOT EXISTS www JSONB NOT NULL DEFAULT '[]';
