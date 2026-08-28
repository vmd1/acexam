-- Phase 4 Postgres Schema

-- Admin role flag. Only admins may access ingestion/review endpoints
-- (paper upload, question editing, publishing, misconception approval).
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false;
