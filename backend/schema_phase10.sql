-- Phase 10 Postgres Schema

-- §6.2 training-example store. `generate_and_validate_synthetic_answers`
-- already produces these at ingestion time, but the results only ever lived
-- in the /upload response body (a count) and were discarded once the
-- request finished -- nothing accumulated into a corpus a future training
-- run could assemble into JSONL. This table is that missing persistence
-- layer, one row per generated synthetic answer.
CREATE TABLE IF NOT EXISTS training_examples (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id                 UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    spec_code                   TEXT,               -- the question's own topic_spec_code at generation time, NULL if unclassified
    candidate_answer            TEXT NOT NULL,
    target_marks                SMALLINT NOT NULL,  -- the mark level the generator was asked to aim for
    awarded_marks               SMALLINT NOT NULL,  -- the independent auto-grader's mark
    is_accepted_for_training    BOOLEAN NOT NULL,   -- true when target/awarded agree within tolerance (§6.2 cross-check)
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_training_examples_question ON training_examples (question_id);

-- Powers training-set assembly: pull the accepted corpus for a spec code,
-- join back to questions/papers for paper series (held-out eval split, §6.5).
CREATE INDEX IF NOT EXISTS idx_training_examples_spec_accepted ON training_examples (spec_code, is_accepted_for_training);
