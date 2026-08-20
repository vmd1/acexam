-- Phase 1 Postgres Schema
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT,                     -- NULL if OAuth-only
    oauth_provider  TEXT,                      -- 'google', NULL for email/password
    oauth_subject   TEXT,                      -- provider's user id
    display_name    TEXT,
    exam_board      TEXT,                      -- e.g. 'AQA', 'Edexcel', 'OCR'
    year_group      TEXT,                      -- e.g. 'GCSE', 'A-Level Y13'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oauth ON users (oauth_provider, oauth_subject)
    WHERE oauth_provider IS NOT NULL;

CREATE TABLE IF NOT EXISTS spec_topics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_board      TEXT NOT NULL,             -- 'AQA'
    subject         TEXT NOT NULL,             -- 'Biology'
    spec_code       TEXT NOT NULL,             -- '4.2.1'
    title           TEXT NOT NULL,             -- 'Cell division'
    parent_id       UUID REFERENCES spec_topics(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_spec_topics_code ON spec_topics (exam_board, subject, spec_code);
