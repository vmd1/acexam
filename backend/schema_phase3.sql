-- ============================================================
-- ATTEMPTS & ANSWERS (student activity)
-- ============================================================

CREATE TABLE IF NOT EXISTS attempts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    paper_id        UUID REFERENCES papers(id),   -- NULL if a custom/generated paper
    source          TEXT NOT NULL DEFAULT 'bank', -- 'bank' | 'custom_generated'
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS answers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id          UUID NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    question_id         UUID NOT NULL REFERENCES questions(id),
    user_id             UUID NOT NULL REFERENCES users(id),
    answer_text         TEXT,
    answer_image_url    TEXT,                  -- set when the answer was submitted as a photo / canvas
    ocr_text            TEXT,                  -- OCR output from answer_image_url
    marks_awarded       SMALLINT,
    marks_possible      SMALLINT NOT NULL,
    feedback_text       TEXT,                  -- populated for AI-marked answers
    missed_points       JSONB,                 -- ["did not mention active transport", ...]
    misconception_tags  JSONB DEFAULT '[]',    -- ["confuses_mitosis_meiosis", ...]
    marked_by           TEXT NOT NULL,         -- 'dsl' | 'ai'
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_answers_attempt ON answers (attempt_id);
CREATE INDEX IF NOT EXISTS idx_answers_user_question ON answers (user_id, question_id);


-- ============================================================
-- MASTER STUDENT PROFILE TABLES (§3.2)
-- ============================================================

-- 1. Topic Mastery & Memory Decay Tree
CREATE TABLE IF NOT EXISTS student_topic_mastery (
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    spec_topic_id       UUID NOT NULL REFERENCES spec_topics(id),
    mastery_score       FLOAT NOT NULL DEFAULT 0.0,   -- EWMA score (0.0 to 1.0)
    attempts_count      INTEGER NOT NULL DEFAULT 0,
    last_practiced_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decay_score         FLOAT NOT NULL DEFAULT 0.0,   -- Ebbinghaus decay score
    PRIMARY KEY (user_id, spec_topic_id)
);

CREATE INDEX IF NOT EXISTS idx_student_topic_decay ON student_topic_mastery (user_id, decay_score ASC);

-- 2. Command Word Competency Matrix
CREATE TABLE IF NOT EXISTS student_command_word_mastery (
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    command_word        TEXT NOT NULL,                -- e.g. 'Evaluate', 'Describe', 'Calculate'
    marks_awarded       INTEGER NOT NULL DEFAULT 0,
    marks_possible      INTEGER NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, command_word)
);

-- 3. Persistent Misconception Memory (§3.7)
CREATE TABLE IF NOT EXISTS student_misconceptions (
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    spec_code           TEXT NOT NULL,
    tag_id              TEXT NOT NULL,                -- references misconception_taxonomy(tag_id)
    occurrences         INTEGER NOT NULL DEFAULT 1,
    consecutive_correct SMALLINT NOT NULL DEFAULT 0,  -- 3 in a row transitions status to 'resolved'
    status              TEXT NOT NULL DEFAULT 'active',-- 'active' | 'resolved'
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, spec_code, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_student_misconceptions_active ON student_misconceptions (user_id, status)
    WHERE status = 'active';

-- ============================================================
-- QUESTION VARIANTS
-- ============================================================

CREATE TABLE IF NOT EXISTS question_variants (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_question_id  UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    variant_text        TEXT NOT NULL,
    images              JSONB NOT NULL DEFAULT '[]',
    marking_type        TEXT NOT NULL,
    marking_dsl         TEXT,
    mark_scheme_text    TEXT,
    validated           BOOLEAN NOT NULL DEFAULT false,
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_question_variants_source ON question_variants (source_question_id);
