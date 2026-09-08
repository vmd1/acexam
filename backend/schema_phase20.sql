-- Phase 20 Postgres Schema

-- AI marking feedback moved from a single freeform feedback_text paragraph
-- to the standard UK classroom "www / ebi" structure (What Went Well /
-- Even Better If), both as bullet-point lists the model returns directly -
-- ebi reuses the existing missed_points column (same meaning: marking
-- points the answer doesn't yet cover), but www has no existing home.
ALTER TABLE training_examples ADD COLUMN IF NOT EXISTS www JSONB NOT NULL DEFAULT '[]';
