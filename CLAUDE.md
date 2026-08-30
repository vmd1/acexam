# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Acexam — a free AI-powered exam revision platform for UK students (AQA/Edexcel/OCR past papers). Students practice real past-paper questions on an exam-paper-style canvas and get AI-marked feedback. See [plan.md](plan.md) for full product spec (ingestion pipeline, marking DSL, misconception tracking, etc.) — read it when working on ingestion or marking logic, it documents the intended design in detail.

Stack: FastAPI + asyncpg (Postgres) + Redis backend, React 19 + TypeScript + Vite frontend, Dockerized for local dev.

## Commands

### Frontend (`frontend/`)
```bash
npm run dev       # vite dev server (proxies /api -> BACKEND_URL, default localhost:8000)
npm run build     # tsc -b && vite build
npm run lint      # oxlint
npm run preview
```

### Backend (`backend/`)
No test suite or lint config currently exists in `backend/`. Run directly with Python 3.11+:
```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Database migrations (raw SQL, no migration framework — see below):
```bash
python migrate.py   # runs schema.sql, schema_phase2.sql ... in order, requires DATABASE_URL
python seed.py       # seeds dev admin account + spec topics, requires DATABASE_URL
```

### Docker (whole stack)
```bash
docker compose up --build
```
Services: `postgres` (5432), `redis` (6379), `backend` (8000, bind-mounted, `--reload`-friendly), `frontend` (5173, bind-mounted). Backend env vars (`JWT_SECRET`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `SEED_ADMIN_EMAIL/PASSWORD`) are set in [docker-compose.yml](docker-compose.yml) and overridable via a root `.env`.

## Architecture

### Database migrations are additive numbered SQL files, not a framework
There is no Alembic/versioned migration tool. Schema changes live in `backend/schema.sql`, `schema_phase2.sql`, ... `schema_phase7.sql`, applied in order by [migrate.py](backend/migrate.py), which hardcodes the filename list and executes each file's full contents against the DB. **Adding a new schema change means creating a new `schema_phaseN.sql` file and adding a corresponding `open()`/`execute()` block to `migrate.py`** — do not edit earlier phase files for anything already deployed.

### Backend request flow
`main.py` wires routers under `/api/*` (`auth`, `exams`, `generate`, `feedback`, `analytics`, `admin/ingestion`) plus a static `/api/media` mount for uploaded images. `database.py` holds a single asyncpg pool (`get_db()`) and a redis client (`get_redis()`) initialized at startup via FastAPI lifespan — both fail soft (log and continue with `None`) if unreachable, so route handlers must check for `None` before using them. Auth is JWT-in-httpOnly-cookie (`dependencies.py`: `get_current_user_id`, `get_current_admin_id`, `rate_limit` — a Redis sliding-window limiter that no-ops if Redis is down).

### The ingestion pipeline is the core piece of domain logic
`ingestion.py::run_full_ai_ingestion_pipeline` coordinates a multi-stage process for turning an uploaded past-paper PDF into structured, gradeable questions:
1. **Visual extraction** (`image_extractor.py`) — dual-mode: PyMuPDF embedded-image extraction plus a vector-graphics fallback (rasterize+crop when boundary detection expects a diagram but no embedded image object is found).
2. **Vision descriptions + table extraction** (pdfplumber) for question context.
3. **Question boundary detection** (regex/heuristics, not AI) splits raw text into questions, including linking a stem's shared diagram to all of its sub-questions.
4. **Marking compilation**: 1–2 mark questions compile to a deterministic DSL (`marking_engine.py::evaluate_dsl_expression` — nested AND/OR/parens over operators like `MCQ`, `CONTAIN`, `ANY`, `RANGE`, `REGEX`); 3+ mark questions store mark-scheme text/image descriptions as context for an LLM call at marking time.
5. Misconception scanning from examiner reports, synthetic training-answer generation for validation.

All LLM calls funnel through `ai_pipeline.py::call_llm`, which supports Gemini (native multi-modal) and OpenAI-compatible endpoints depending on which API key env vars are set, with per-request token accounting via a `contextvars.ContextVar` (isolated per concurrent ingestion request, not a module global — don't change this to a plain global).

Admin-only ingestion/review endpoints live in `routers/ingestion.py`; regular exam/practice endpoints in `routers/exams.py`, `routers/generate.py`, `routers/feedback.py`.

### Frontend
Single-page React app (`App.tsx`) with client-side routing. Route guards are composed wrappers around `useAuth()` (`AuthContext.tsx`): `ProtectedRoute` (must be logged in), `RequireSubjects` (must also have onboarded with ≥1 subject, else redirected to `/onboarding`), `AdminRoute` (must have `is_admin`). Auth state is httpOnly-cookie based — `api.ts` sets `withCredentials: true` on a shared axios instance; there is no token stored in JS. Vite dev server proxies `/api` to the backend (see `vite.config.ts`), so the frontend always talks to a relative `/api/...` path, never an absolute backend URL.

Key views: `PracticeSetup`/`PracticeSession` (student practice flow), `ExamCanvas` (paper-style question rendering/answering), `AdminIngestion`/`AdminReview` (past-paper upload + review console), `AnalyticsView` (weakness tracking), `Onboarding`/`SubjectsManager`/`ManageAccount`.
