-- Phase 24 Postgres Schema

-- Prompt/weights pairing: records the marking_prompt.py hash (see
-- backend/marking_prompt.py::prompt_hash) an adapter was actually trained
-- against, written by training/agent.py::process_training_job when it gates
-- a completed run. NULL for any adapter trained before this column existed
-- (the three adapters already on disk this session) - a NULL is treated as
-- "unknown, skip the mismatch check" rather than a false-positive warning
-- for legacy adapters. Compared against the CURRENT prompt_hash() at serve
-- time (training/agent.py::_resolve_adapter) so a live adapter silently
-- diverging from the prompt it was trained against - the actual root cause
-- suspected in the list-scheme regression this phase's fixes were added
-- for - surfaces as a loud log line instead of only being discoverable via
-- manual browser testing days later.
ALTER TABLE spec_code_marking_models ADD COLUMN IF NOT EXISTS active_adapter_prompt_hash TEXT;

-- Shadow-mode rollout: an admin can set shadow_adapter_version on a spec
-- code that already has status='live' to have training/agent.py dual-run a
-- sample of real incoming marking requests through both the live and
-- candidate adapters, logging both outputs (shadow_marking_comparisons
-- below) without ever serving the shadow adapter's result to a student.
-- NULL (the default) means no shadow adapter is active for this spec code.
ALTER TABLE spec_code_marking_models ADD COLUMN IF NOT EXISTS shadow_adapter_version TEXT;

CREATE TABLE IF NOT EXISTS shadow_marking_comparisons (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_board              TEXT NOT NULL,
    level                   TEXT NOT NULL,
    subject                 TEXT NOT NULL,
    tier                    TEXT NOT NULL DEFAULT '',
    live_adapter_version    TEXT,
    shadow_adapter_version  TEXT,
    live_result             JSONB,      -- best-effort parsed {marks_awarded, www, ebi} from the live adapter's raw output
    shadow_result           JSONB,      -- same shape from the shadow adapter's raw output on the SAME request
    marks_agree             BOOLEAN,    -- live_result.marks_awarded == shadow_result.marks_awarded (NULL if either failed to parse)
    shadow_contradictory    BOOLEAN,    -- shadow_result violates the full-marks/zero-marks ebi/www rule (raw, pre-guardrail)
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shadow_marking_comparisons_spec_code
    ON shadow_marking_comparisons (exam_board, level, subject, tier, created_at DESC);
