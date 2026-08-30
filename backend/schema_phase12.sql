-- Phase 12 Postgres Schema

-- Custom paper generation previously hardcoded its target mark total (the
-- frontend always sent target_marks: 20, and the backend didn't even read
-- it) and derived its timer purely from whatever marks ended up selected.
-- Real papers have a fixed, board-set mark total and time allowance (e.g.
-- AQA GCSE Biology Higher: 100 marks, 1h45) - store that per qualification
-- so an admin can configure it once via Manage Subjects, instead of it
-- being a magic number in code. NULL means "not configured yet", in which
-- case the generator falls back to its previous heuristic.
ALTER TABLE qualifications ADD COLUMN IF NOT EXISTS custom_paper_target_marks INTEGER;
ALTER TABLE qualifications ADD COLUMN IF NOT EXISTS custom_paper_time_limit_minutes INTEGER;
