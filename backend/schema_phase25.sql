-- Phase 25 Postgres Schema

-- A question can genuinely test more than one specification topic - a
-- multi-part stem's sub-questions often span different spec points, and
-- even a single sub-question can require content from two topics at once
-- (e.g. a calculation that draws on both a maths-skills topic and the
-- biology topic it's applied to). questions.spec_topic_id only ever
-- captured ONE match, silently discarding every other topic the AI
-- splitter identified. This join table captures the FULL set.
--
-- questions.spec_topic_id is kept as-is (the first/primary match, still
-- populated by ingestion) so every existing single-topic consumer -
-- mastery tracking (profile_engine.py), analytics grouping
-- (routers/analytics.py), and the topic name shown alongside a question -
-- keeps working completely unchanged. Topic FILTERING (adaptive queue /
-- custom paper topic selection in routers/generate.py) is upgraded
-- separately to match against this table instead, so a multi-topic
-- question surfaces under any topic it actually covers, not just its
-- primary one.
CREATE TABLE IF NOT EXISTS question_topics (
    question_id     UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    spec_topic_id   UUID NOT NULL REFERENCES spec_topics(id),
    PRIMARY KEY (question_id, spec_topic_id)
);

CREATE INDEX IF NOT EXISTS idx_question_topics_topic ON question_topics (spec_topic_id);

-- Backfill: every question already classified under a single spec_topic_id
-- gets that one topic recorded here too, so the topic-filter queries above
-- see every pre-existing question, not just ones ingested after this
-- migration.
INSERT INTO question_topics (question_id, spec_topic_id)
SELECT id, spec_topic_id FROM questions WHERE spec_topic_id IS NOT NULL
ON CONFLICT DO NOTHING;
