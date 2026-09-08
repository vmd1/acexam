-- Phase 17 Postgres Schema

-- §6.5 training-request queue for the Mac-native MLX agent
-- (backend/training/agent.py). Admin clicks "Request training" in
-- AdminSubjectDetail -> a row lands here as 'queued'; the agent (a separate
-- host process, not this backend) polls for queued rows and walks each one
-- through assemble_dataset.py -> train_lora.py -> evaluate.py, updating
-- status/log/eval_result as it goes and (on success) spec_code_marking_models
-- itself (status -> 'gated', never 'live' - that stays an explicit admin
-- action via the existing PUT /marking-models). One row per training
-- *attempt*, not per spec code, so retraining keeps a full history/log trail
-- instead of overwriting the previous run's outcome.
CREATE TABLE IF NOT EXISTS training_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_board      TEXT NOT NULL,
    level           TEXT NOT NULL,
    subject         TEXT NOT NULL,
    tier            TEXT NOT NULL DEFAULT '',
    base_model      TEXT NOT NULL DEFAULT 'mlx-community/Qwen2.5-1.5B-Instruct-4bit',
    status          TEXT NOT NULL DEFAULT 'queued',
                    -- queued | assembling | training | evaluating | done | failed
    adapter_version TEXT,               -- set once train_lora.py's adapter dir is known
    eval_result     JSONB,              -- evaluate.py output for this run
    log             TEXT NOT NULL DEFAULT '',   -- appended to as the agent progresses
    error           TEXT,
    requested_by    UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_training_jobs_status ON training_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_training_jobs_spec_code
    ON training_jobs (exam_board, level, subject, tier, created_at DESC);
