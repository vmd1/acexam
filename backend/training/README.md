# Training/serving pipeline (Mac-native)

Everything in this directory runs on the host Mac, not in Docker — MLX is
Metal-backed (Apple Silicon only), and the rest of the stack runs in Linux
containers (see the root `docker-compose.yml`).

- `assemble_dataset.py` / `train_lora.py` / `evaluate.py` — the offline
  LoRA fine-tuning pipeline (§6.5). Still runnable by hand.
- `agent.py` — the long-running process with three jobs:
  1. Drives the above from admin UI "Request training" clicks (polls the
     `training_jobs` table).
  2. Dynamically loads/evicts `mlx_lm.server` processes on demand as
     marking requests arrive, capped at `MAX_CONCURRENT_SERVERS` residents
     at once (least-recently-used model evicted to make room) — with dozens
     of `live` spec codes and one Mac's worth of unified memory, keeping
     everything permanently loaded doesn't fit.
  3. A janitor sweep that stops anything no longer `live`, drops (without
     restarting) anything that crashed, and proactively evicts anything
     idle past `IDLE_EVICT_SECONDS`.

  Run it manually: `python agent.py` (see `.env.example` for config; no
  launchd/auto-start — start it yourself when you want training/serving
  available).

**No HTTP between this agent and the backend, in either direction, for
anything** — not marking requests, not status. `agent.py` talks to Postgres
directly (`training_jobs`, `spec_code_marking_models` — same ports
`docker-compose.yml` already publishes to the host) and to Redis directly
as a work queue: the backend `RPUSH`es a marking job onto `mlx:marking:queue`
and `BLPOP`s the matching `mlx:marking:result:<id>` key; this agent `BLPOP`s
the queue and `RPUSH`es the result once it has one (see `MLX_QUEUE_KEY` /
`MLX_RESULT_KEY_PREFIX` in `agent.py` and `backend/ai_pipeline.py` — those
two key names are the entire interface between the two processes and must
match exactly). Redis list operations are atomic, so this is a correct
multi-producer/multi-consumer queue: any number of backend replicas can
enqueue, any number of agent replicas (e.g. more than one Mac) can consume,
with zero direct coupling or knowledge of each other — each side scales
independently. Jobs for different spec codes are handled concurrently by
each agent instance, not queued up behind each other's model-load latency.

The backend only knows about `training_jobs`/`spec_code_marking_models` as
plain tables it reads and writes via its own admin endpoints;
`routers/internal.py`'s Traefik config endpoint (legacy fallback path, see
`ai_pipeline.py`) picks up whatever `serving_port` the agent has written the
same way it always has, but isn't on the hot path while the Redis queue is
reachable.

Install this directory's own deps into a separate venv (kept out of
`backend/requirements.txt` since `mlx-lm` won't install on the backend
container's Linux image):

```bash
cd backend/training
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit if your local Postgres/Redis setup differs
python agent.py
```
