-- Phase 5 Postgres Schema

-- A user can be studying multiple subject/board/level combinations at once
-- (e.g. AQA GCSE Biology and OCR A-Level Chemistry), so this is a separate
-- table rather than the single exam_board/year_group columns on users.
CREATE TABLE IF NOT EXISTS user_subjects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exam_board      TEXT NOT NULL,
    level           TEXT NOT NULL,             -- e.g. 'GCSE', 'A-Level'
    subject         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, exam_board, level, subject)
);
