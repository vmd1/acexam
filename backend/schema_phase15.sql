-- Phase 15 Postgres Schema

-- §6.3/§6.4 rollout gating. Nothing until now tracked whether a spec code's
-- self-hosted marking model has ever been trained, let alone whether it has
-- cleared the held-out eval bar and is allowed to serve live 3+ mark
-- answers - marking_engine.mark_question always fell through to the AI path
-- for any marking_type='ai' question regardless of model readiness. This
-- table is the missing gate: keyed at the same granularity training runs at
-- (exam_board/level/subject/tier, §6.1 - NOT the fine-grained
-- spec_topics.spec_code training_examples rows carry), one row per spec
-- code, admin-set status controlling whether feedback.py is allowed to call
-- the self-hosted model at all for that spec code.
CREATE TABLE IF NOT EXISTS spec_code_marking_models (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_board              TEXT NOT NULL,
    level                   TEXT NOT NULL,
    subject                 TEXT NOT NULL,
    tier                    TEXT NOT NULL DEFAULT '',
    status                  TEXT NOT NULL DEFAULT 'none',
                            -- 'none' | 'training' | 'gated' | 'live' (§6.3/§6.4)
                            -- none: no adapter trained yet, AI marking unavailable, falls back to needs_review
                            -- training: a checkpoint exists but hasn't cleared held-out eval
                            -- gated: cleared held-out eval, not yet flipped on for live traffic
                            -- live: serving real student answers via the self-hosted adapter
    active_adapter_version  TEXT,               -- e.g. 'v-20260831T112955Z', matches training/adapters/<slug>/<version>
    eval_result             JSONB,               -- last evaluate.py output (§6.5) for the active version
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_spec_code_marking_models_key
    ON spec_code_marking_models (exam_board, level, subject, tier);
