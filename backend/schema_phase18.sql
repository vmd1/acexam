-- Phase 18 Postgres Schema

-- §6.5 auto-triggered retraining (backend/training/agent.py::auto_train_loop).
-- To decide "has enough *new* accepted training data accumulated since this
-- spec code's last training run to justify another one" without a second
-- scan/join at decision time, each job records how many accepted
-- training_examples existed for its spec code at the moment it was created
-- (whether admin-requested or auto-triggered) - the next scan just compares
-- the current count against this anchor.
ALTER TABLE training_jobs ADD COLUMN IF NOT EXISTS accepted_examples_at_request INTEGER;
