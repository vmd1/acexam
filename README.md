# Acexam

A free AI-powered exam revision platform for UK students. Students practice
real AQA/Edexcel/OCR past-paper questions on an exam-paper-style canvas and
get instant, mark-scheme-accurate AI feedback — no subscriptions, no paywalls.

See [plan.md](plan.md) for the full product spec (ingestion pipeline,
marking DSL, misconception tracking, etc.) and [CLAUDE.md](CLAUDE.md) for a
map of the codebase's architecture.

## Stack

- **Backend**: FastAPI + asyncpg (Postgres) + Redis
- **Frontend**: React 19 + TypeScript + Vite
- **Storage**: S3-compatible object storage (MinIO locally) for extracted
  question images and uploaded PDFs, served to the browser only through an
  authenticated backend proxy
- **AI**: past-paper ingestion (splitting, marking-scheme compilation,
  misconception scanning) via Gemini or an OpenAI-compatible endpoint; live
  answer marking is either a deterministic rule DSL or a self-hosted
  fine-tuned model — a configured hosted API key is never used for marking
  student answers
- Dockerized for local dev, fronted by Traefik

## Quickstart (Docker)

```bash
cp backend/.env.example .env   # then set MINIO_ROOT_USER / MINIO_ROOT_PASSWORD at minimum
docker compose up --build
```

Add `127.0.0.1 acexam.localhost` to `/etc/hosts`, then visit
`http://acexam.localhost`. The API is served under `http://acexam.localhost/api`.

Services: `postgres` (5432), `redis` (6379), `minio` (S3-compatible storage,
routed via Traefik), `traefik` (80, reverse proxy + internal marking-server
routing), `backend` (2 replicas, bind-mounted, `--reload`-friendly), `frontend`
(Vite dev server, bind-mounted). Migrations and dev seed data run
automatically on backend startup.

Backend environment variables (`JWT_SECRET`, `GEMINI_API_KEY`,
`OPENAI_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `SEED_ADMIN_EMAIL/PASSWORD`,
`MINIO_ROOT_USER/PASSWORD`, `S3_BUCKET`, `TRAEFIK_INTERNAL_TOKEN`) are set in
[docker-compose.yml](docker-compose.yml) and overridable via a root `.env`.
None of these have insecure defaults for anything beyond local dev — see the
`:?` guards in the compose file for values you must set yourself.

## Manual dev setup

### Frontend (`frontend/`)

```bash
npm install
npm run dev       # vite dev server, proxies /api -> BACKEND_URL (default localhost:8000)
npm run build     # tsc -b && vite build
npm run lint       # oxlint
npm run preview
```

### Backend (`backend/`)

Python 3.11+, no test suite or lint config currently exists:

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL / REDIS_URL / JWT_SECRET etc.
python migrate.py      # applies schema.sql, schema_phase2.sql, ... in order
python seed.py          # seeds a dev admin account + spec topics
uvicorn main:app --reload --port 8000
```

There is no migration framework — schema changes are additive, numbered
`schema_phaseN.sql` files applied in order by `migrate.py`. See
[CLAUDE.md](CLAUDE.md) for the full schema reference and which phase file
owns which table.

### Self-hosted marking agent (`backend/training/`)

Optional, host-native (macOS/MLX-only) process that trains and serves
lightweight fine-tuned marking models per spec code. See
`backend/training/.env.example` for its configuration and
[plan.md §6.5](plan.md) for the design.

```bash
cd backend/training
cp .env.example .env
python agent.py
```

## Security notes

- Auth is JWT-in-httpOnly-cookie (`JWT_SECRET` — the dev fallback in code
  **must** be overridden in any non-local deployment).
- `.env` files are gitignored; only `.env.example` templates (placeholder
  values only) are committed.
- Uploaded past-paper PDFs and extracted question images are exam-board
  copyrighted material — they're stored in a private bucket and served only
  through the backend's authenticated `/api/media` proxy, never a public S3
  URL.
