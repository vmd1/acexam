-- Phase 13 Postgres Schema

-- training_examples so far only ever came from generate_and_validate_synthetic_answers
-- (a purely AI-invented candidate answer). Examiner reports quote/describe real
-- candidate answers with commentary on why they scored what they did -- higher-signal
-- §6.2 seed data than a synthetic one, extracted via
-- generate_exemplar_training_examples_from_report and blind-graded the same way, but
-- worth telling apart from synthetic examples when assembling/weighting training sets.
ALTER TABLE training_examples ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'synthetic';
