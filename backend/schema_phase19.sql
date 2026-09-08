-- Phase 19 Postgres Schema

-- attempts previously only tracked (user_id, paper_id, source) with no way
-- to tell a saved custom paper or an "exam questions" practice session apart
-- from a one-off bank answer, and no record of which questions belonged to
-- it. These columns plus attempt_questions turn attempts into the real
-- "saved paper / session" persistence unit the practice UI revamp needs.
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS mode TEXT;              -- 'adaptive' | 'exam_questions' | 'custom'
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS subject TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS exam_board TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS level TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS time_limit_seconds INT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS total_marks INT;

-- Frozen question set for an attempt (in serving order), so a saved custom
-- paper or exam-questions session can be reopened later and show exactly
-- the questions it originally contained.
CREATE TABLE IF NOT EXISTS attempt_questions (
    attempt_id  UUID NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    position    INT NOT NULL,
    PRIMARY KEY (attempt_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_attempt_questions_attempt ON attempt_questions (attempt_id, position);
CREATE INDEX IF NOT EXISTS idx_attempts_user_mode ON attempts (user_id, mode, started_at DESC);
