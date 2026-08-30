-- Phase 6 Postgres Schema

-- Topics are scoped per qualification level too (e.g. AQA offers different
-- Biology content at GCSE vs A-Level), so the subject-picker can cascade
-- Level -> Exam Board -> Subject using only combinations that actually exist.
ALTER TABLE spec_topics ADD COLUMN IF NOT EXISTS level TEXT NOT NULL DEFAULT 'GCSE';

DROP INDEX IF EXISTS idx_spec_topics_code;
CREATE UNIQUE INDEX IF NOT EXISTS idx_spec_topics_code ON spec_topics (exam_board, subject, level, spec_code);
