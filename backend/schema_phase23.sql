-- Phase 23 Postgres Schema

-- Subject page "grade strip" (next to the subject name): a student's own
-- target grade, plus an optional manually-entered grade from their most
-- recent real-world test/mock - separate from predicted_grade (computed
-- live from grade_boundaries + practice accuracy in routers/analytics.py),
-- since a real exam result isn't something this app can derive on its own.
-- show_latest_test_grade lets the student hide that field from the subject
-- page without losing the value, rather than forcing them to delete it.
ALTER TABLE user_subjects ADD COLUMN IF NOT EXISTS target_grade TEXT;
ALTER TABLE user_subjects ADD COLUMN IF NOT EXISTS latest_test_grade TEXT;
ALTER TABLE user_subjects ADD COLUMN IF NOT EXISTS show_latest_test_grade BOOLEAN NOT NULL DEFAULT false;
