-- Phase 16 Postgres Schema

-- §6.5's stated multi-adapter serving gap: mlx-lm has no built-in router
-- across adapters, one process serves one loaded model. The chosen
-- workaround is one `mlx_lm.server` process per live spec code, each on
-- its own host port, with a Traefik proxy in front dispatching by spec
-- code path prefix (/ai/mark/<slug>/...) - see backend/traefik/ and
-- training/generate_traefik_config.py. This column is what ties a spec
-- code's gating row to the actual host port its server process is
-- listening on, so the Traefik config generator has something to read.
ALTER TABLE spec_code_marking_models ADD COLUMN IF NOT EXISTS serving_port INTEGER;
