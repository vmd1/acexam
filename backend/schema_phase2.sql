-- Phase 2 Postgres Schema

-- MISCONCEPTION TAXONOMY (canonical tags per spec code)
CREATE TABLE IF NOT EXISTS misconception_taxonomy (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    spec_code       TEXT NOT NULL,              -- e.g. '8462/H' or '4.2.1'
    tag_id          TEXT NOT NULL,              -- e.g. 'confuses_mitosis_meiosis'
    label           TEXT NOT NULL,              -- 'Confuses mitosis and meiosis'
    description     TEXT NOT NULL,              -- 'Mistakes process steps or cell outcome between mitosis and meiosis'
    approved_at     TIMESTAMPTZ,                -- NULL if pending admin approval
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_misconception_tax_spec_tag ON misconception_taxonomy (spec_code, tag_id);


-- PAPERS & QUESTIONS (ingestion output)
CREATE TABLE IF NOT EXISTS papers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_board      TEXT NOT NULL,
    subject         TEXT NOT NULL,
    paper_code      TEXT,                      -- e.g. '8461/1H'
    series          TEXT,                      -- e.g. 'June 2023'
    source_pdf_url  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'processing',
                    -- 'processing' | 'needs_review' | 'published' | 'failed'
    uploaded_by     UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS questions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id            UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    question_number     TEXT NOT NULL,          -- '3(b)(ii)'
    mark_value          SMALLINT NOT NULL,
    question_text       TEXT NOT NULL,
    images              JSONB NOT NULL DEFAULT '[]',
                        -- [{ "url": "...", "bbox": [x0,y0,x1,y1], "checksum": "...", "description": "..." }, ...]
    table_data          JSONB,                 -- pdfplumber-extracted tables, if any
    spec_topic_id        UUID REFERENCES spec_topics(id),
    marking_type        TEXT NOT NULL,          -- 'dsl' | 'ai'
    marking_dsl         TEXT,                   -- e.g. 'ANY:glucose,sugar AND CONTAIN:respiration'
    mark_scheme_text    TEXT,                   -- raw text, used for AI marking (3+ mark questions)
    needs_review        BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_questions_paper ON questions (paper_id);
CREATE INDEX IF NOT EXISTS idx_questions_spec_topic ON questions (spec_topic_id);
CREATE INDEX IF NOT EXISTS idx_questions_filter ON questions (mark_value, marking_type);
