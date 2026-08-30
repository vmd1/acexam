-- Phase 7 Postgres Schema

-- Not every question is best answered as free text. The AI ingestion
-- pipeline classifies each question's answer_type so the frontend can
-- render the right input widget instead of always forcing a textbox:
--   written      - free text / ink canvas (default, e.g. Explain/Describe/Evaluate)
--   select       - single choice, e.g. "Tick one box" (answer_options: [{key, text}])
--   multi_select - multiple choice, e.g. "Tick two boxes" (answer_options: [{key, text}])
--   numeric      - a single calculated value, optionally with a unit (answer_options: {unit})
--   grid_select  - one choice per row/statement, e.g. "Tick True or False for each row"
--                  (answer_options: [{statement, options: [string, ...]}])
ALTER TABLE questions ADD COLUMN IF NOT EXISTS answer_type TEXT NOT NULL DEFAULT 'written';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS answer_options JSONB;
