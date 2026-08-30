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

#### Schema reference (which phase file owns which table)
- **schema.sql**: `users` (incl. `password_hash`, `is_admin` added in phase4), `spec_topics` (hierarchical via `parent_id`; scoped by `(exam_board, subject, level, spec_code)` since phase6, `level` column added phase6).
- **schema_phase2.sql**: `misconception_taxonomy` (canonical tag catalogue per spec_code, `approved_at NULL` = pending admin approval), `papers` (`status`: `processing|needs_review|published|failed`), `questions` (marking fields, `images` JSONB, `needs_review`; `answer_type`/`answer_options` JSONB added phase7).
- **schema_phase3.sql**: `attempts`, `answers` (one row per submission attempt, `marked_by: 'dsl'|'ai'`, `missed_points`/`misconception_tags` JSONB — this is the source of truth for "has the student answered this before"), `student_topic_mastery` (composite PK `user_id,spec_topic_id`), `student_command_word_mastery` (composite PK `user_id,command_word`), `student_misconceptions` (composite PK `user_id,spec_code,tag_id`, `status: active|resolved`), `question_variants`.
- **schema_phase4.sql**: `users.is_admin`.
- **schema_phase5.sql**: `user_subjects` (id, user_id, exam_board, level, subject, UNIQUE on all four) — backs onboarding, a student can have multiple subject/board/level combos.
- **schema_phase6.sql**: `spec_topics.level`, re-scopes the topic uniqueness index to include it.
- **schema_phase7.sql**: `questions.answer_type` + `questions.answer_options` (so the frontend renders the right input widget instead of always a free-text box).

JSONB columns (`images`, `answer_options`, `missed_points`, `misconception_tags`) come back from asyncpg as **raw JSON text**, not decoded objects (no custom codec registered on the pool) — every route that returns a Record straight from the DB passes these through as strings, and the frontend re-`JSON.parse`s them (see e.g. `ExamCanvas.tsx`'s `imagesList`/`answerOptions` parsing, or `parseJsonMaybe` for `previous_answer`). Don't assume a `dict(record)` result already has these decoded.

### Backend request flow
`main.py` wires routers under `/api/*`: `auth`, `exams`, `generate`, `feedback`, `analytics`, `admin/ingestion` (mounted at that admin-only prefix), plus a static `/api/media` mount for uploaded images. `database.py` holds a single asyncpg pool (`get_db()`) and a redis client (`get_redis()`) initialized at startup via FastAPI lifespan — both fail soft (log and continue with `None`) if unreachable, so route handlers must check for `None` before using them.

Auth is JWT-in-httpOnly-cookie, all in [dependencies.py](backend/dependencies.py):
- `get_current_user_id(request)` — sync, reads the `access_token` cookie, `jwt.decode`s it (HS256, `JWT_SECRET` env — **dev fallback `"supersecretkey_for_dev_only"`, must be overridden in prod**), returns the `sub` claim; 401 if missing/invalid. Tokens expire after 1 week (`create_access_token`).
- `get_current_admin_id(request)` — async, calls the above then checks `users.is_admin`; 403 if falsy.
- `rate_limit(request, limit=60, window=60)` — Redis fixed-window counter keyed by user id (falls back to client IP for anonymous callers); **fails open** (silently no-ops) if Redis is unreachable or errors.

Routers, endpoint-by-endpoint:
- **`routers/auth.py`** — `POST /register`, `/login` (sets the cookie), `/logout`, `GET /me`, `PATCH /me` (partial profile update), `POST /me/password`, and full CRUD on `/me/subjects` (`user_subjects`, upserted via `ON CONFLICT (user_id, exam_board, level, subject)`). No `rate_limit` dependency on this router (unlike the others). `/google` and `/google/callback` are unimplemented stubs.
- **`routers/exams.py`** — public read-only bank browsing: `GET /papers`, `/paper/{id}`, `/question/{id}`, `/topics`. **No `paper.status = 'published'` filter** — same gap exists in `generate.py`'s queries, so an unpublished/`needs_review` paper's questions are technically fetchable by a student if referenced directly.
- **`routers/generate.py`** — practice session generation, see dedicated section below.
- **`routers/feedback.py`** — `POST /submit`: marks an answer (`marking_engine.mark_question`), inserts a row into `answers`, creates an `attempts` row if none passed, and updates the Master Student Profile (`profile_engine.update_student_profile_after_answer`). This is the only place answers get written.
- **`routers/analytics.py`** — `GET /profile`: recomputes decay scores live (`profile_engine.calculate_decayed_mastery_scores`) then returns topic mastery tree, command-word accuracy matrix, active/resolved misconceptions, and a hardcoded GCSE 9-1 grade-boundary heuristic (Python constant table, not configurable per subject/board).
- **`routers/ingestion.py`** (admin-only, `Depends(get_current_admin_id)` at router level) — `POST /upload` (multipart QP + optional mark scheme/examiner report, runs the full ingestion pipeline, inserts `papers` as `needs_review` + its `questions` + proposed misconceptions), `POST /upload-spec` (parses a spec PDF into `spec_topics`, two-pass insert to wire `parent_id`), `GET /papers`, `GET /paper/{id}`, `PUT /question/{id}` (manual edit), `POST /paper/{id}/publish`, `GET /misconceptions`, `POST /misconception/approve`. Uploaded PDFs aren't stored anywhere real (`mock_s3_url` is a fake string, not object storage).

### The ingestion pipeline is the core piece of domain logic
`ingestion.py::run_full_ai_ingestion_pipeline` coordinates a multi-stage process for turning an uploaded past-paper PDF into structured, gradeable questions:
1. **Visual extraction** (`image_extractor.py`) — dual-mode: PyMuPDF embedded-image extraction plus a vector-graphics fallback (rasterize+crop when boundary detection expects a diagram but no embedded image object is found).
2. **Vision descriptions + table extraction** (pdfplumber) for question context.
3. **Question boundary detection**: prefers AI-driven joint splitting (`ai_pipeline.py::split_paper_into_questions`, which also isolates each question's own mark scheme text and per-question metadata — `answer_type`/`answer_options`, `topic_spec_code`, `references_figure`/`figure_label`); falls back to `detect_question_boundaries` (regex/heuristics) only if the AI call fails/returns nothing. Images are linked to the question(s) that reference them via a page-range heuristic, and captioned with the exact figure label the question text uses to refer to them (`figure_label` from the AI split, or a `Fig(?:ure)?\.?\s*\d+` regex fallback in the non-AI path) — each linked image gets a **shallow copy** per question so sibling sub-questions referencing the same shared stem diagram don't clobber each other's captions.
4. **Marking compilation**: 1–2 mark questions compile to a deterministic DSL (see Marking & scoring below); 3+ mark questions store mark-scheme text/image descriptions as context for an LLM call at marking time.
5. Misconception scanning from examiner reports, synthetic training-answer generation for validation.

All LLM calls funnel through `ai_pipeline.py::call_llm`, which supports Gemini (native multi-modal) and OpenAI-compatible endpoints depending on which API key env vars are set, with per-request token accounting via a `contextvars.ContextVar` (isolated per concurrent ingestion request, not a module global — don't change this to a plain global).

### Marking & scoring
`marking_engine.py::mark_question` picks DSL vs AI marking based on `marking_type`/`marking_dsl` (DSL when both are truthy, typically 1-2 mark questions; otherwise `ai_pipeline.mark_with_selfhosted_model` — comments explicitly warn never to point that at a hosted API). `evaluate_dsl_expression` is a small recursive-descent parser: splits top-level `OR` then `AND` (paren-depth aware), evaluates leaf clauses via operators `MCQ:`, `CONTAIN:`, `NOT CONTAIN:`, `ANY:` (comma list, any match), `ALL:` (comma list, all required), `EXACT:` (numeric, 1e-5 tolerance), `RANGE:min,max` (numeric), `REGEX:` — an unrecognized operator fails closed (`False`), never open. If the AI marking response fails to parse as JSON, there's a crude keyword-overlap fallback scorer with a few hardcoded biology-specific misconception heuristics (e.g. `mitosis`+`gamete` co-occurrence) — a known stopgap, not a general mechanism.

`profile_engine.py` maintains the "Master Student Profile" shown in `AnalyticsView`: `update_student_profile_after_answer` (called from `feedback.py::submit`) does an EWMA update to `student_topic_mastery.mastery_score` (`α=0.35`), additive accumulation into `student_command_word_mastery`, and misconception bookkeeping (triggered tags bump `occurrences`/reset streak to `active`; a ≥90%-scoring answer that triggers no misconceptions increments `consecutive_correct` on all active misconceptions for that spec_code, auto-resolving at streak ≥3). `calculate_decayed_mastery_scores` applies Ebbinghaus decay (`mastery_score * e^(-days_elapsed/14)`) **live on every analytics/queue fetch**, not via a cron/stored decay — there's no background job.

### Practice queue generation (`routers/generate.py`)
`GET /adaptive-queue` (topic/misconception-targeted) and `POST /custom-paper` (subject/board/topic-filtered) both select candidate **individual question rows** first (topic filters apply per sub-question, since sub-parts of one stem can each be classified under a different spec topic), then run them through two fixups before returning:
1. **`_expand_to_full_groups`** — a multi-part question (`08.1`, `08.2`, ... sharing one stem) must never be served with early sub-parts missing just because only a later part matched the topic filter. Candidates are expanded to every sibling sharing `(paper_id, root_number)`, where the root is the leading digits of `question_number` (`_question_root`, mirrors `PracticeSession.tsx`'s `questionRootKey` exactly — keep these two in sync if either changes). Grouping is scoped to `paper_id` since two different papers can both have a "question 1".
2. **Mastery exclusion + previous-answer attachment** — `MASTERY_EXCLUSION_SQL` (`NOT EXISTS ... answers WHERE marks_awarded = marks_possible`) excludes a question from **initial candidate selection** once the student has ever scored full marks on it, so a mastered question is never freshly served again. It can still reappear as an already-expanded sibling of a still-unfinished group (intentional — the group renders complete). `_attach_previous_answers` then fetches each returned question's most recent `answers` row (`DISTINCT ON (question_id) ... ORDER BY created_at DESC`) and attaches it as `previous_answer`, so the frontend can prefill/review prior work instead of a blank slate — this is the mechanism behind "don't re-serve finished questions" + "remember my answers".

### Frontend
Single-page React app (`App.tsx`) with client-side routing. Route guards are composed wrappers around `useAuth()` (`AuthContext.tsx`, which also exposes `hasSubjects` fetched from `/auth/me/subjects`): `ProtectedRoute` (must be logged in), `RequireSubjects` (must also have onboarded with ≥1 subject, else redirected to `/onboarding`), `AdminRoute` (must have `is_admin`). Auth state is httpOnly-cookie based — `api.ts` sets `withCredentials: true` on a shared axios instance; there is no token stored in JS. Vite dev server proxies `/api` to the backend (see `vite.config.ts`), so the frontend always talks to a relative `/api/...` path, never an absolute backend URL. `App.tsx` also owns the light/dark theme toggle (`data-theme` attribute + `localStorage`, defaults to `prefers-color-scheme`).

Key views:
- **`PracticeSetup`/`PracticeSession`** — practice entry point (adaptive queue vs custom paper by subject/topic) and the session runner. `PracticeSession.tsx::groupQuestions`/`questionRootKey` groups the flat question list from the API into per-stem pages for `ExamCanvas` — **must stay in sync with `_question_root` in `generate.py`** (same leading-digits logic).
- **`ExamCanvas`** — paper-style question rendering/answering/marking UI, one instance per question group. Notable behaviors: renders `question_text`/mark scheme through a shared `Markdown` wrapper that runs `formatUnits` (unit exponents like `dm3`→`dm³` via Unicode superscript) and `formatBullets` (`formatUnits.ts`) (turns stray `•`-delimited runs from ingestion into real Markdown list items) before handing off to `react-markdown`; shows each linked image with its ingested caption; keeps the student's submitted answer (typed/MCQ/numeric/grid text, or a canvas-drawing snapshot taken at submit time) visible inside the marking feedback panel instead of hiding it; and on mount, prefills state from each question's `previous_answer` (full marks → shown as already-graded; partial marks → editable prefill + a review banner with "View previous feedback" / "Keep this score & continue").
- **`AdminIngestion`/`AdminReview`** — past-paper/spec upload forms and the review-and-publish console (papers tab + misconceptions-approval tab).
- **`AnalyticsView`** — renders `/analytics/profile`; groups topic mastery via `topicHierarchy.ts::groupByTopicHierarchy` (BFS over `spec_topics.parent_id`, falls back to flat grouping for legacy data with no real hierarchy) and color-codes by mastery threshold.
- **`Onboarding`/`SubjectsManager`/`ManageAccount`** — `SubjectsManager` is the shared add/edit/remove UI for `user_subjects` (level→board→subject options derived from `/exams/topics`'s actual distinct combos, not a hardcoded list), used by both `Onboarding` (first-run gate, blocks continuing until ≥1 subject) and `ManageAccount` (also hosts profile edit + password change).
