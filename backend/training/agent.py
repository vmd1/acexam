"""
§6.5 Mac-native MLX agent: the process that actually runs on the training
Mac (MLX is Metal-backed, native macOS only - the rest of this app runs in
Linux containers, see docker-compose.yml). Four concurrent pieces:

1. training_job_loop - polls the `training_jobs` queue (schema_phase17.sql,
   filled by POST /admin/qualifications/training-jobs OR auto_train_loop
   below) and walks each queued row through assemble_dataset.py ->
   train_lora.py -> evaluate.py in turn, one job at a time (a single Mac has
   one GPU). train_lora.py itself picks LoRA rank/epochs/LR from that spec
   code's training-set size (see train_lora.py::pick_hyperparameters) rather
   than a single fixed config, so a brand-new subject with a small corpus
   and a long-ingested one with a large corpus each get a sensible setup
   automatically. On success it gates the corresponding
   spec_code_marking_models row to 'gated' with the new adapter/eval
   attached - it never sets 'live', that stays an explicit admin action via
   the existing PUT /admin/qualifications/marking-models.

2. auto_train_loop - the "onboard a new subject in minutes" piece: scans
   for spec codes that have cleared AUTO_TRAIN_MIN_EXAMPLES accepted
   training_examples but have never been trained, or have accumulated
   AUTO_RETRAIN_NEW_EXAMPLES more since their last run (or that run is
   older than AUTO_RETRAIN_MAX_AGE_DAYS), and enqueues a training_jobs row
   for them automatically - no "Request training" click needed. Since
   ingestion already generates training_examples per-question at upload
   time (§6.2), the practical effect is: create the subject, upload its
   past papers through the existing admin ingestion pipeline, and its first
   adapter trains and gates itself. See CANDIDATE_SPEC_CODES_SQL.

3. queue_worker_loop - the "dynamic loading" piece. The backend never talks
   to this process over HTTP (see below) - instead it RPUSHes a marking job
   onto a shared Redis list (MLX_QUEUE_KEY) and BLPOPs the matching result
   key. This agent BLPOPs that same list, and for each job dispatches a task
   (not a blocking call - see below) that ensures the right spec code's
   mlx_lm.server is resident, loading it on demand if it isn't - evicting
   the least-recently-used resident model to make room if the pool is
   already at MAX_CONCURRENT_SERVERS - runs the completion against it, and
   RPUSHes the result back. A warm spec code answers in milliseconds; a cold
   one pays a model-load latency cost once. Jobs for *different* spec codes
   are handled concurrently (each dispatched as its own task the instant
   it's dequeued), not queued up behind each other.

4. sweep_loop - a lower-frequency janitor: stops any resident server whose
   spec code is no longer 'live', drops (without restarting) anything that
   crashed, and proactively evicts anything that's sat idle past
   IDLE_EVICT_SECONDS even though nothing forced it out yet - so memory
   gets freed as demand quiets down, not just when a new model needs the
   slot.

This process talks to Postgres directly (DATABASE_URL) and Redis directly
(REDIS_URL) - both default to the same instances docker-compose.yml already
publishes to the host (localhost:5432 / localhost:6379). There is
deliberately NO HTTP between this process and the backend, in either
direction, for anything - not the marking requests, not status. That keeps
the two sides free to scale independently: any number of backend replicas
can RPUSH onto the same queue, any number of agent replicas can BLPOP from
it (whichever one is free picks up the next job - Redis's list pop is
atomic, so this is a correct multi-consumer work queue with zero direct
coupling), and neither side needs to know how many of the other exists or
where it's running. (Admin UI state - training-job status,
spec_code_marking_models - is still observed as plain Postgres tables both
sides already read/write, same as before; that was never HTTP either.)

Run manually: `python agent.py` (no launchd/auto-start - see
backend/training/requirements.txt for this process's own deps, kept
separate from backend/requirements.txt since mlx-lm is Metal-only and
would break the Linux container build).
"""
import asyncio
import json
import os
import re
import signal
import socket
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

# spec_code_slug is defined once in backend/spec_code_utils.py and must
# never drift from that copy - it's what both training/adapters/<slug>/ and
# incoming queue jobs' spec_slug field are keyed on.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spec_code_utils import spec_code_slug  # noqa: E402
from marking_prompt import prompt_hash as current_prompt_hash  # noqa: E402

from train_lora import ADAPTERS_ROOT, DATA_ROOT, DEFAULT_MODEL  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://acexam:acexam@localhost:5432/acexam")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
TRAINING_POLL_INTERVAL = float(os.environ.get("TRAINING_POLL_INTERVAL", "5"))
SWEEP_INTERVAL = float(os.environ.get("SWEEP_INTERVAL", os.environ.get("RECONCILE_POLL_INTERVAL", "15")))
PORT_RANGE_START = int(os.environ.get("MLX_SERVER_PORT_RANGE_START", "8100"))
PORT_RANGE_END = int(os.environ.get("MLX_SERVER_PORT_RANGE_END", "8199"))
MAX_CONCURRENT_SERVERS = int(os.environ.get("MAX_CONCURRENT_SERVERS", "8"))
HEALTH_CHECK_TIMEOUT = float(os.environ.get("MLX_HEALTH_CHECK_TIMEOUT", "45"))
UNHEALTHY_RETRY_BACKOFF = float(os.environ.get("MLX_UNHEALTHY_RETRY_BACKOFF", "60"))
IDLE_EVICT_SECONDS = float(os.environ.get("IDLE_EVICT_SECONDS", "600"))

# §6.5 "let ingestion handle everything automatically, then train the
# models" - a new subject (e.g. Physics) should need nothing beyond papers
# uploaded through the existing admin ingestion pipeline (which already
# generates training_examples per-question at ingestion time, §6.2) to end
# up with a trained, gated adapter - no admin clicking "Request training".
# See auto_train_loop. Thresholds mirror plan.md §6.5's documented
# retraining cadence ("200+ new examples or monthly, whichever first").
AUTO_TRAIN_ENABLED = os.environ.get("AUTO_TRAIN_ENABLED", "true").lower() not in ("false", "0", "")
AUTO_TRAIN_MIN_EXAMPLES = int(os.environ.get("AUTO_TRAIN_MIN_EXAMPLES", "50"))
AUTO_RETRAIN_NEW_EXAMPLES = int(os.environ.get("AUTO_RETRAIN_NEW_EXAMPLES", "200"))
AUTO_RETRAIN_MAX_AGE_DAYS = float(os.environ.get("AUTO_RETRAIN_MAX_AGE_DAYS", "30"))
AUTO_TRAIN_SCAN_INTERVAL = float(os.environ.get("AUTO_TRAIN_SCAN_INTERVAL", "600"))

# Must match ai_pipeline.py's MLX_QUEUE_KEY / MLX_RESULT_KEY_PREFIX exactly -
# these are the only "interface" between the two processes.
MLX_QUEUE_KEY = "mlx:marking:queue"
MLX_RESULT_KEY_PREFIX = "mlx:marking:result:"
MLX_RESULT_TTL = 120  # seconds a result sits in Redis if nobody collects it

TRAINING_DIR = Path(__file__).resolve().parent

IN_FLIGHT_TRAINING_STATUSES = ("queued", "assembling", "training", "evaluating")

# Module-level singleton state, set up in main() and shared between the
# training loop, the sweep loop, and the /ensure HTTP handler. This process
# is a single instance by design (a Mac has one GPU) so plain module state
# is simpler than threading a context object through everywhere.
#
# Loading is deliberately NOT serialized behind one global lock: the Mac
# this runs on can genuinely load/serve several models at once (up to
# MAX_CONCURRENT_SERVERS), so N simultaneous cold /ensure calls for N
# different spec codes should load in parallel, not queue up one-at-a-time
# behind each other's ~2s load latency. What still needs to be serialized is
# just the *bookkeeping* - deciding whether there's room, and which model to
# evict if not - which is why POOL_STATE_LOCK below only ever guards a brief
# synchronous-ish section, never the actual subprocess spawn/health-check.
POOL: asyncpg.Pool = None
RUNNING: dict = {}          # slug -> RunningServer (fully loaded and resident)
RESERVED: dict = {}         # slug -> port, currently mid-load (reserved a pool slot, not yet in RUNNING)
UNHEALTHY_BACKOFF: dict = {}  # slug -> loop.time() before which we won't retry
POOL_STATE_LOCK = asyncio.Lock()  # guards RUNNING/RESERVED bookkeeping + eviction decisions only
LOADING_LOCKS: dict = {}    # slug -> asyncio.Lock(), dedupes concurrent ensure() calls for the *same* slug


def loading_lock_for(slug):
    lock = LOADING_LOCKS.get(slug)
    if lock is None:
        lock = LOADING_LOCKS[slug] = asyncio.Lock()
    return lock


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def slug_for(row):
    return spec_code_slug(row["exam_board"], row["level"], row["subject"], row["tier"])


# ---------------------------------------------------------------------------
# Training-job loop (unaffected by dynamic loading - separate subprocess,
# separate resource footprint from the serving pool below)
# ---------------------------------------------------------------------------

async def run_subprocess_streamed(cmd, cwd, job_id, pool):
    """
    Runs cmd, appending combined stdout/stderr into training_jobs.log
    incrementally (so the admin UI's polling log view shows live progress
    rather than only the final blob), and returns (returncode, tail_of_output).
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    tail_lines = []
    async for raw_line in proc.stdout:
        line = raw_line.decode(errors="replace").rstrip("\n")
        tail_lines.append(line)
        tail_lines = tail_lines[-200:]
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE training_jobs SET log = log || $1 WHERE id = $2",
                line + "\n", job_id,
            )
    returncode = await proc.wait()
    return returncode, "\n".join(tail_lines[-40:])


async def set_marking_model_status(conn, row, status, **fields):
    # $1-$5 are the INSERT's own positional args (exam_board, level, subject,
    # tier, status); the SET clause re-references fresh params appended after
    # those, starting at $6, so `values` (not the INSERT's $5) is what the
    # UPDATE branch actually reads on conflict.
    columns = ["status"] + list(fields.keys())
    values = [status] + list(fields.values())
    set_clause = ", ".join(f"{c} = ${i + 6}" for i, c in enumerate(columns))
    await conn.execute(
        f'''
        INSERT INTO spec_code_marking_models (exam_board, level, subject, tier, status)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (exam_board, level, subject, tier) DO UPDATE SET
            {set_clause}, updated_at = now()
        ''',
        row["exam_board"], row["level"], row["subject"], row["tier"], status, *values,
    )


async def process_training_job(pool, job):
    job_id = job["id"]
    slug = slug_for(job)
    base_model = job["base_model"] or DEFAULT_MODEL
    log(f"[train {slug}] starting job {job_id}")

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE training_jobs SET status = 'assembling', started_at = now() WHERE id = $1", job_id
        )
        # Only flip to 'training' if nothing is currently live - get_marking_model_row
        # serves exclusively status='live' rows, so flipping a live spec code away from
        # 'live' just because a (possibly unrelated/exploratory) retrain started would
        # block real marking requests for the entire training run even though the live
        # adapter on disk is untouched until this job's result is explicitly promoted.
        # training_jobs.status (not this row) is what already gates "job in flight" for
        # the admin UI and the duplicate-request 409 check.
        current_status = await conn.fetchval(
            '''SELECT status FROM spec_code_marking_models
               WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4''',
            job["exam_board"], job["level"], job["subject"], job["tier"],
        )
        if current_status != "live":
            await set_marking_model_status(conn, job, "training")

    # assemble_dataset.py has no per-spec-slug filter today - it rebuilds
    # every spec code's JSONL in one pass, which is idempotent and cheap
    # enough (a DB query plus file writes) to run per job as-is.
    returncode, tail = await run_subprocess_streamed(
        [sys.executable, "assemble_dataset.py"], TRAINING_DIR, job_id, pool,
    )
    if returncode != 0:
        await fail_job(pool, job, "assemble_dataset.py failed:\n" + tail)
        return
    if not (DATA_ROOT / slug / "train.jsonl").exists():
        await fail_job(pool, job, f"No accepted training examples for {slug} after assembly")
        return

    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # "v-" prefixed everywhere this gets stored/displayed/resolved-from-disk
    # (spec_code_marking_models.active_adapter_version, training_jobs.
    # adapter_version, the admin UI) - matches the actual adapter directory
    # name train_lora.py creates. Only the bare `version` (no prefix) goes
    # to train_lora.py's --version flag below, since it prepends "v-" itself.
    adapter_version = f"v-{version}"
    adapter_path = ADAPTERS_ROOT / slug / adapter_version

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE training_jobs SET status = 'training' WHERE id = $1", job_id
        )
    returncode, tail = await run_subprocess_streamed(
        [sys.executable, "train_lora.py", slug, "--model", base_model, "--version", version],
        TRAINING_DIR, job_id, pool,
    )
    if returncode != 0:
        await fail_job(pool, job, "train_lora.py failed:\n" + tail)
        return

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE training_jobs SET status = 'evaluating', adapter_version = $1 WHERE id = $2",
            adapter_version, job_id,
        )
    returncode, tail = await run_subprocess_streamed(
        [sys.executable, "evaluate.py", slug, "--adapter-path", str(adapter_path), "--model", base_model],
        TRAINING_DIR, job_id, pool,
    )
    if returncode != 0:
        await fail_job(pool, job, "evaluate.py failed:\n" + tail)
        return

    eval_result = None
    eval_path = adapter_path / "eval_result.json"
    if eval_path.exists():
        try:
            eval_result = json.loads(eval_path.read_text())
        except json.JSONDecodeError:
            eval_result = None

    # Pairs this adapter with the exact marking_prompt.py it was trained
    # against (assemble_dataset.py just built this job's corpus from the
    # current prompt, moments before train_lora.py ran) - compared against
    # the live prompt hash at serve time in _resolve_adapter below, so a
    # future prompt edit that silently diverges from what a live adapter was
    # actually trained on surfaces as a loud log line instead of only being
    # discoverable via manual testing, which is what happened this session.
    trained_prompt_hash = current_prompt_hash()
    run_meta_path = adapter_path / "run_meta.json"
    if run_meta_path.exists():
        try:
            meta = json.loads(run_meta_path.read_text())
            meta["prompt_hash"] = trained_prompt_hash
            run_meta_path.write_text(json.dumps(meta, indent=2))
        except json.JSONDecodeError:
            pass

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE training_jobs SET status = 'done', eval_result = $1, finished_at = now() WHERE id = $2",
            json.dumps(eval_result) if eval_result is not None else None, job_id,
        )
        await set_marking_model_status(
            conn, job, "gated",
            active_adapter_version=adapter_version,
            active_adapter_prompt_hash=trained_prompt_hash,
            eval_result=json.dumps(eval_result) if eval_result is not None else None,
        )
    log(f"[train {slug}] job {job_id} done -> gated, adapter {adapter_version} (prompt hash {trained_prompt_hash})")


async def fail_job(pool, job, error):
    slug = slug_for(job)
    log(f"[train {slug}] job {job['id']} failed: {error[:400]}")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE training_jobs SET status = 'failed', error = $1, finished_at = now() WHERE id = $2",
            error, job["id"],
        )
        # Only revert to 'none' if this job is what put it into 'training' -
        # don't clobber a 'gated'/'live' status left by an earlier successful
        # run if this was a retrain attempt.
        current = await conn.fetchval(
            '''SELECT status FROM spec_code_marking_models
               WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4''',
            job["exam_board"], job["level"], job["subject"], job["tier"],
        )
        if current == "training":
            await set_marking_model_status(conn, job, "none")


async def recover_stale_jobs(pool):
    """
    A job left in an in-flight status (assembling/training/evaluating) at
    startup means the previous agent process died or was killed mid-run -
    there's no in-progress subprocess to reattach to, so the honest outcome
    is 'failed', not silently stuck forever with its "Request training"
    button permanently disabled.
    """
    async with pool.acquire() as conn:
        stale = await conn.fetch(
            '''SELECT * FROM training_jobs WHERE status = ANY($1::text[])''',
            list(IN_FLIGHT_TRAINING_STATUSES[1:]),  # not 'queued' - those just haven't started yet
        )
    for row in stale:
        job = dict(row)
        log(f"[train {slug_for(job)}] job {job['id']} was left {job['status']} by a previous agent run - marking failed")
        await fail_job(pool, job, "Agent restarted while this job was in progress")


CANDIDATE_SPEC_CODES_SQL = '''
    SELECT
        p.exam_board, p.level, p.subject, COALESCE(p.tier, '') AS tier,
        m.status,
        COUNT(te.id) FILTER (WHERE te.is_accepted_for_training) AS accepted_count
    FROM (SELECT DISTINCT exam_board, level, subject, tier FROM papers) p
    LEFT JOIN spec_code_marking_models m
        ON m.exam_board = p.exam_board AND m.level = p.level
        AND m.subject = p.subject AND m.tier = COALESCE(p.tier, '')
    LEFT JOIN questions q ON q.paper_id IN (
        SELECT id FROM papers pp
        WHERE pp.exam_board = p.exam_board AND pp.level = p.level
        AND pp.subject = p.subject AND COALESCE(pp.tier, '') = COALESCE(p.tier, '')
    )
    LEFT JOIN training_examples te ON te.question_id = q.id
    GROUP BY p.exam_board, p.level, p.subject, p.tier, m.status
'''


async def auto_train_loop(pool):
    """
    §6.5 automatic onboarding: every AUTO_TRAIN_SCAN_INTERVAL, look at every
    spec code that actually has ingested papers and decide whether it's due
    a training run, with no admin action required -

    - never trained (status IS NULL/'none') and has cleared
      AUTO_TRAIN_MIN_EXAMPLES accepted training_examples -> first train.
    - already trained ('gated'/'live') and has accumulated
      AUTO_RETRAIN_NEW_EXAMPLES more accepted examples than its last
      training job started with, OR that last job is older than
      AUTO_RETRAIN_MAX_AGE_DAYS -> retrain (same cadence plan.md §6.5
      documents: "200+ new examples or monthly, whichever comes first").

    Never touches a spec code with a job already in flight (same guard as
    the admin-facing POST /training-jobs endpoint), and - like every other
    path that creates a training run - never sets status to 'live' itself;
    a retrain only ever lands back at 'gated' pending the admin's explicit
    re-promotion.
    """
    if not AUTO_TRAIN_ENABLED:
        log("[auto-train] disabled (AUTO_TRAIN_ENABLED=false)")
        return

    while True:
        try:
            async with pool.acquire() as conn:
                candidates = await conn.fetch(CANDIDATE_SPEC_CODES_SQL)
                for row in candidates:
                    exam_board, level, subject, tier = row["exam_board"], row["level"], row["subject"], row["tier"]
                    status = row["status"] or "none"
                    accepted_count = row["accepted_count"] or 0

                    in_flight = await conn.fetchval(
                        '''SELECT 1 FROM training_jobs
                           WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4
                             AND status = ANY($5::text[]) LIMIT 1''',
                        exam_board, level, subject, tier, list(IN_FLIGHT_TRAINING_STATUSES),
                    )
                    if in_flight:
                        continue

                    last_job = await conn.fetchrow(
                        '''SELECT * FROM training_jobs
                           WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4
                           ORDER BY created_at DESC LIMIT 1''',
                        exam_board, level, subject, tier,
                    )

                    should_train, reason = False, None
                    if status == "none" and last_job is None and accepted_count >= AUTO_TRAIN_MIN_EXAMPLES:
                        should_train, reason = True, f"first train: {accepted_count} accepted examples"
                    elif status in ("gated", "live") and last_job is not None:
                        anchor = last_job["accepted_examples_at_request"] or 0
                        age_days = (datetime.now(timezone.utc) - last_job["created_at"]).total_seconds() / 86400
                        new_examples = accepted_count - anchor
                        if new_examples >= AUTO_RETRAIN_NEW_EXAMPLES:
                            should_train, reason = True, f"retrain: {new_examples} new accepted examples since last run"
                        elif age_days >= AUTO_RETRAIN_MAX_AGE_DAYS:
                            should_train, reason = True, f"retrain: last run was {age_days:.0f} days ago"

                    if should_train:
                        slug = spec_code_slug(exam_board, level, subject, tier)
                        log(f"[auto-train {slug}] {reason} - enqueueing")
                        await conn.execute(
                            '''INSERT INTO training_jobs
                                   (exam_board, level, subject, tier, base_model, accepted_examples_at_request)
                               VALUES ($1, $2, $3, $4, $5, $6)''',
                            exam_board, level, subject, tier, DEFAULT_MODEL, accepted_count,
                        )
        except Exception as exc:  # noqa: BLE001 - keep the loop alive across transient errors
            log(f"[auto-train] loop error: {exc!r}")

        await asyncio.sleep(AUTO_TRAIN_SCAN_INTERVAL)


async def training_job_loop(pool):
    await recover_stale_jobs(pool)
    while True:
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    job = await conn.fetchrow(
                        '''SELECT * FROM training_jobs WHERE status = 'queued'
                           ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED'''
                    )
                    if job:
                        # Claim it immediately inside the same transaction so a
                        # second agent instance can't also pick it up.
                        await conn.execute(
                            "UPDATE training_jobs SET status = 'assembling' WHERE id = $1", job["id"]
                        )
            if job:
                await process_training_job(pool, dict(job))
            else:
                await asyncio.sleep(TRAINING_POLL_INTERVAL)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive across transient errors
            log(f"[train] loop error: {exc!r}")
            await asyncio.sleep(TRAINING_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Dynamic model pool: on-demand loading + LRU eviction
# ---------------------------------------------------------------------------

class RunningServer:
    def __init__(self, process, port, adapter_version, key):
        self.process = process
        self.port = port
        self.adapter_version = adapter_version
        # (exam_board, level, subject, tier) - kept on the handle itself so a
        # server can still have its serving_port cleared on stop without a
        # caller needing to hand in a fresh DB row. None for a shadow-mode
        # resident (ensure_shadow_model) - a shadow server has no live-facing
        # serving_port column to clear, see _stop_locked's guard below.
        self.key = key
        self.last_used = asyncio.get_event_loop().time()


def free_port(taken_ports):
    for port in range(PORT_RANGE_START, PORT_RANGE_END + 1):
        if port in taken_ports:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return None


async def wait_for_health(port, timeout):
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient() as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(f"http://127.0.0.1:{port}/v1/models", timeout=2)
                if resp.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    return False


async def get_marking_model_row(slug):
    async with POOL.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM spec_code_marking_models WHERE status = 'live'")
    for row in rows:
        if slug_for(row) == slug:
            return dict(row)
    return None


_WARNED_PROMPT_MISMATCH: set = set()  # slugs already warned this process run, so it doesn't spam every request


def _resolve_adapter(slug, row):
    """Cheap, synchronous - no reason to hold any lock or reserve a slot for this."""
    if not row["active_adapter_version"]:
        log(f"[serve {slug}] no active_adapter_version set, cannot start")
        return None, None
    adapter_path = ADAPTERS_ROOT / slug / row["active_adapter_version"]
    if not adapter_path.exists():
        log(f"[serve {slug}] adapter path missing: {adapter_path}")
        return None, None
    base_model = DEFAULT_MODEL
    run_meta_path = adapter_path / "run_meta.json"
    if run_meta_path.exists():
        try:
            base_model = json.loads(run_meta_path.read_text()).get("base_model", DEFAULT_MODEL)
        except json.JSONDecodeError:
            pass

    # A NULL active_adapter_prompt_hash means this adapter predates the
    # column (nothing to compare against - skip silently rather than warn on
    # every legacy adapter). A mismatch means the live marking_prompt.py has
    # since diverged from what this adapter was actually fine-tuned to
    # expect - not blocked outright (a loud, repeated log line is safer than
    # silently refusing live marking over a hash check), but this is exactly
    # the failure mode that produced this session's list-scheme regression,
    # so it's worth surfacing immediately rather than only via manual
    # testing days later.
    trained_hash = row.get("active_adapter_prompt_hash")
    if trained_hash and trained_hash != current_prompt_hash() and slug not in _WARNED_PROMPT_MISMATCH:
        log(
            f"[serve {slug}] WARNING: live marking_prompt.py (hash {current_prompt_hash()}) does not "
            f"match the prompt adapter {row['active_adapter_version']} was trained against "
            f"(hash {trained_hash}) - this adapter's behavior may not match what its training corpus "
            f"taught it. Retrain, or restore the prompt this adapter expects."
        )
        _WARNED_PROMPT_MISMATCH.add(slug)

    return adapter_path, base_model


async def _stop_locked(slug, clear_port=True):
    """Must be called with POOL_STATE_LOCK held."""
    handle = RUNNING.pop(slug, None)
    if not handle:
        return
    log(f"[serve {slug}] stopping server on port {handle.port}")
    if handle.process.returncode is None:
        handle.process.terminate()
        try:
            await asyncio.wait_for(handle.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            handle.process.kill()
            await handle.process.wait()
    if clear_port and handle.key is not None:
        async with POOL.acquire() as conn:
            await conn.execute(
                '''UPDATE spec_code_marking_models SET serving_port = NULL, updated_at = now()
                   WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4''',
                *handle.key,
            )


async def _reserve_slot(slug):
    """
    Must be called with POOL_STATE_LOCK held. Evicts the least-recently-used
    *resident* model if the pool is full, then claims a port for `slug` so no
    other concurrent load can pick the same one. Returns the reserved port,
    or None if there's genuinely no room (everything resident is itself
    mid-eviction/reservation, which shouldn't normally happen).
    """
    while len(RUNNING) + len(RESERVED) >= MAX_CONCURRENT_SERVERS:
        evictable = [s for s in RUNNING if s not in RESERVED]
        if not evictable:
            return None
        lru_slug = min(evictable, key=lambda s: RUNNING[s].last_used)
        log(f"[serve] pool full ({MAX_CONCURRENT_SERVERS}), evicting LRU model {lru_slug} to make room for {slug}")
        await _stop_locked(lru_slug)

    taken_ports = {s.port for s in RUNNING.values()} | set(RESERVED.values())
    port = free_port(taken_ports)
    if port is None:
        log(f"[serve {slug}] no free port in range {PORT_RANGE_START}-{PORT_RANGE_END}")
        return None
    RESERVED[slug] = port
    return port


async def _load_reserved(slug, row, adapter_path, base_model, port):
    """
    The slow part (subprocess spawn + health check) - deliberately NOT run
    under POOL_STATE_LOCK, so concurrent loads for other spec codes aren't
    blocked behind this one. Only the brief commit/rollback at the end takes
    the lock again.
    """
    log(f"[serve {slug}] loading mlx_lm.server on port {port} (adapter {row['active_adapter_version']})")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "mlx_lm", "server",
        "--model", base_model,
        "--adapter-path", str(adapter_path),
        # 0.0.0.0, not the mlx_lm.server default of 127.0.0.1 - this process
        # must be reachable from the backend's Docker container via
        # host.docker.internal, which arrives as a real TCP connection, not
        # literally loopback.
        "--host", "0.0.0.0",
        "--port", str(port),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    healthy = await wait_for_health(port, HEALTH_CHECK_TIMEOUT)

    async with POOL_STATE_LOCK:
        RESERVED.pop(slug, None)
        if not healthy:
            log(f"[serve {slug}] never became healthy on port {port}, killing")
            process.kill()
            await process.wait()
            UNHEALTHY_BACKOFF[slug] = asyncio.get_event_loop().time() + UNHEALTHY_RETRY_BACKOFF
            return None
        key = (row["exam_board"], row["level"], row["subject"], row["tier"])
        RUNNING[slug] = RunningServer(process, port, row["active_adapter_version"], key)
        UNHEALTHY_BACKOFF.pop(slug, None)

    async with POOL.acquire() as conn:
        await conn.execute(
            '''UPDATE spec_code_marking_models SET serving_port = $1, updated_at = now()
               WHERE exam_board = $2 AND level = $3 AND subject = $4 AND tier = $5''',
            port, *key,
        )
    log(f"[serve {slug}] ready on port {port}")
    return port


async def ensure_model(slug):
    """
    The dynamic-loading entry point: called by the backend (via the /ensure
    HTTP endpoint below) right before a marking request for this spec code.
    Returns the port to send the completion request to, loading the model on
    demand - evicting the least-recently-used resident model first if the
    pool is already at MAX_CONCURRENT_SERVERS - or None if it isn't 'live',
    has no adapter, or failed to come up healthy.

    Concurrent ensure() calls for *different* slugs load in parallel (only
    the brief pool-capacity bookkeeping is serialized, not the actual model
    load) - this Mac can genuinely run several loads/models at once, so
    several students hitting several different cold spec codes at the same
    moment shouldn't queue up behind each other. Concurrent calls for the
    *same* slug are deduped via a per-slug lock so only one of them actually
    triggers a load.
    """
    loop = asyncio.get_event_loop()
    if loop.time() < UNHEALTHY_BACKOFF.get(slug, 0):
        return None

    row = await get_marking_model_row(slug)
    if row is None:
        return None

    def fresh_hit():
        handle = RUNNING.get(slug)
        if handle is not None and handle.process.returncode is None and handle.adapter_version == row["active_adapter_version"]:
            handle.last_used = loop.time()
            return handle.port
        return None

    port = fresh_hit()
    if port is not None:
        return port

    async with loading_lock_for(slug):
        # Re-check now that we hold the per-slug lock - another concurrent
        # call for this exact slug may have just finished loading it while
        # we were waiting.
        port = fresh_hit()
        if port is not None:
            return port

        if slug in RUNNING:
            # Dead, or serving a stale adapter version - drop before reloading.
            async with POOL_STATE_LOCK:
                await _stop_locked(slug, clear_port=False)

        adapter_path, base_model = _resolve_adapter(slug, row)
        if adapter_path is None:
            return None

        async with POOL_STATE_LOCK:
            port = await _reserve_slot(slug)
        if port is None:
            return None

        return await _load_reserved(slug, row, adapter_path, base_model, port)


def _resolve_shadow_adapter(slug, shadow_version):
    """Same shape as _resolve_adapter but for an arbitrary version string
    (not gated on status='live') - a shadow adapter is deliberately allowed
    to be a 'gated' candidate that hasn't been promoted yet."""
    adapter_path = ADAPTERS_ROOT / slug / shadow_version
    if not adapter_path.exists():
        log(f"[shadow {slug}] adapter path missing: {adapter_path}")
        return None, None
    base_model = DEFAULT_MODEL
    run_meta_path = adapter_path / "run_meta.json"
    if run_meta_path.exists():
        try:
            base_model = json.loads(run_meta_path.read_text()).get("base_model", DEFAULT_MODEL)
        except json.JSONDecodeError:
            pass
    return adapter_path, base_model


async def ensure_shadow_model(slug, shadow_version):
    """
    Loads/serves a candidate adapter alongside the live one for `slug`,
    reusing the same RUNNING/RESERVED pool bookkeeping (eviction, port
    allocation) as ensure_model - keyed under a distinct
    f"{slug}::shadow:{version}" pool slot so it can never collide with or
    evict the live resident for the same spec code. Never touches
    spec_code_marking_models.serving_port (that column tracks the LIVE
    server only - RunningServer.key is None for a shadow resident, and
    _stop_locked already skips the DB write when key is None). Returns the
    shadow server's port, or None if it isn't on disk / never came up
    healthy.
    """
    shadow_slug = f"{slug}::shadow:{shadow_version}"
    loop = asyncio.get_event_loop()

    def fresh_hit():
        handle = RUNNING.get(shadow_slug)
        if handle is not None and handle.process.returncode is None:
            handle.last_used = loop.time()
            return handle.port
        return None

    port = fresh_hit()
    if port is not None:
        return port

    async with loading_lock_for(shadow_slug):
        port = fresh_hit()
        if port is not None:
            return port

        if shadow_slug in RUNNING:
            async with POOL_STATE_LOCK:
                await _stop_locked(shadow_slug, clear_port=False)

        adapter_path, base_model = _resolve_shadow_adapter(slug, shadow_version)
        if adapter_path is None:
            return None

        async with POOL_STATE_LOCK:
            port = await _reserve_slot(shadow_slug)
        if port is None:
            return None

        log(f"[shadow {slug}] loading mlx_lm.server on port {port} (adapter {shadow_version})")
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "mlx_lm", "server",
            "--model", base_model,
            "--adapter-path", str(adapter_path),
            "--host", "0.0.0.0",
            "--port", str(port),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        healthy = await wait_for_health(port, HEALTH_CHECK_TIMEOUT)

        async with POOL_STATE_LOCK:
            RESERVED.pop(shadow_slug, None)
            if not healthy:
                log(f"[shadow {slug}] never became healthy on port {port}, killing")
                process.kill()
                await process.wait()
                return None
            RUNNING[shadow_slug] = RunningServer(process, port, shadow_version, key=None)
        log(f"[shadow {slug}] ready on port {port}")
        return port


async def sweep_loop(pool):
    """
    Lower-frequency janitor pass: stop anything no longer 'live', drop (but
    don't restart) anything that crashed, and proactively evict anything
    idle past IDLE_EVICT_SECONDS - freeing memory as demand quiets down
    rather than only when a new model needs the slot.
    """
    async with pool.acquire() as conn:
        # A previous agent run's serving_port values can't be trusted until
        # re-verified by this process.
        await conn.execute("UPDATE spec_code_marking_models SET serving_port = NULL WHERE serving_port IS NOT NULL")

    try:
        while True:
            try:
                async with pool.acquire() as conn:
                    live_rows = await conn.fetch("SELECT * FROM spec_code_marking_models WHERE status = 'live'")
                live_slugs = {slug_for(r) for r in live_rows}
                # A shadow resident's pool key (f"{slug}::shadow:{version}")
                # never appears in live_slugs by construction, so without
                # this it would get stopped as "not live" on every sweep
                # cycle. Keep it alive only while its spec code's
                # shadow_adapter_version still matches the version this
                # resident was loaded for - an admin clearing or changing
                # the shadow version makes the old resident stale and it
                # should be dropped, same as any other config change.
                eligible_shadow_slugs = {
                    f"{slug_for(r)}::shadow:{r['shadow_adapter_version']}"
                    for r in live_rows if r["shadow_adapter_version"]
                }
                now = asyncio.get_event_loop().time()

                async with POOL_STATE_LOCK:
                    for slug, handle in list(RUNNING.items()):
                        if handle.process.returncode is not None:
                            log(f"[serve {slug}] process exited unexpectedly (code {handle.process.returncode})")
                            await _stop_locked(slug)
                        elif slug not in live_slugs and slug not in eligible_shadow_slugs:
                            await _stop_locked(slug)
                        elif now - handle.last_used > IDLE_EVICT_SECONDS:
                            log(f"[serve {slug}] idle for {int(now - handle.last_used)}s, evicting")
                            await _stop_locked(slug)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive across transient errors
                log(f"[sweep] loop error: {exc!r}")

            await asyncio.sleep(SWEEP_INTERVAL)
    except asyncio.CancelledError:
        log(f"Stopping {len(RUNNING)} running mlx_lm.server process(es)...")
        async with POOL_STATE_LOCK:
            for slug in list(RUNNING.keys()):
                await _stop_locked(slug)
        raise


# ---------------------------------------------------------------------------
# Redis queue worker: consumes marking jobs, produces results. No HTTP.
# ---------------------------------------------------------------------------

async def call_local_completion(port, system_prompt, prompt):
    """
    The actual model call - always loopback (127.0.0.1), since the
    mlx_lm.server process this talks to is a child of this same agent
    process. This is not backend<->agent traffic; it never leaves the Mac.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            json={
                # mlx_lm.server maps this sentinel to whatever
                # --model/--adapter-path it was started with - anything else
                # makes it try to fetch that string as a fresh Hugging Face
                # repo id.
                "model": "default_model",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 768,
            },
        )
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]


def _best_effort_parse_marking_json(raw):
    """
    Lightweight, self-contained mirror of marking_engine.py's
    _extract_marking_json - agent.py deliberately doesn't import
    marking_engine.py (that module pulls in the full backend dependency set,
    e.g. sympy, which training/requirements.txt intentionally keeps separate
    from backend/requirements.txt). Only needs to be good enough to recover
    marks_awarded/www/ebi for shadow-mode comparison logging, not to be the
    canonical parser real grading depends on.
    """
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None


async def _run_shadow_comparison(slug, job, live_content):
    """
    Fire-and-forget: runs the SAME live marking request through a candidate
    ("shadow") adapter, if one is configured for this spec code, and logs
    both outputs for offline comparison - never blocks or affects the real
    response, which has already been RPUSHed back to the backend before
    this is even scheduled (see handle_marking_job). This is what lets a
    promotion decision be based on real traffic shape (agent.py already
    runs several mlx_lm.server processes concurrently, see ensure_model)
    rather than only the held-out eval set.
    """
    try:
        row = await get_marking_model_row(slug)
        if not row or not row.get("shadow_adapter_version"):
            return
        shadow_version = row["shadow_adapter_version"]
        shadow_port = await ensure_shadow_model(slug, shadow_version)
        if shadow_port is None:
            return
        shadow_content = await call_local_completion(shadow_port, job["system_prompt"], job["prompt"])

        live_parsed = _best_effort_parse_marking_json(live_content)
        shadow_parsed = _best_effort_parse_marking_json(shadow_content)

        marks_agree = None
        if isinstance(live_parsed, dict) and isinstance(shadow_parsed, dict):
            lm, sm = live_parsed.get("marks_awarded"), shadow_parsed.get("marks_awarded")
            if isinstance(lm, int) and isinstance(sm, int):
                marks_agree = lm == sm

        shadow_contradictory = None
        if isinstance(shadow_parsed, dict):
            sm = shadow_parsed.get("marks_awarded")
            mark_value_match = re.search(r"Total Marks:\s*(\d+)", job.get("prompt") or "")
            mark_value = int(mark_value_match.group(1)) if mark_value_match else None
            if isinstance(sm, int):
                full_contradiction = mark_value is not None and sm >= mark_value and len(shadow_parsed.get("ebi") or []) > 0
                zero_contradiction = sm == 0 and len(shadow_parsed.get("www") or []) > 0
                shadow_contradictory = bool(full_contradiction or zero_contradiction)

        async with POOL.acquire() as conn:
            await conn.execute(
                '''INSERT INTO shadow_marking_comparisons
                    (exam_board, level, subject, tier, live_adapter_version, shadow_adapter_version,
                     live_result, shadow_result, marks_agree, shadow_contradictory)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)''',
                row["exam_board"], row["level"], row["subject"], row["tier"],
                row["active_adapter_version"], shadow_version,
                json.dumps(live_parsed) if live_parsed is not None else None,
                json.dumps(shadow_parsed) if shadow_parsed is not None else None,
                marks_agree, shadow_contradictory,
            )
    except Exception as exc:  # noqa: BLE001 - shadow mode must never affect the live marking path
        log(f"[shadow {slug}] comparison error: {type(exc).__name__}: {exc}")


async def handle_marking_job(r, job):
    """
    Runs as its own task per job (see queue_worker_loop) so N jobs for N
    different spec codes proceed concurrently instead of queueing behind
    each other - the dequeue loop's only job is to keep dequeuing quickly.
    """
    job_id = job["id"]
    slug = job["spec_slug"]
    try:
        port = await ensure_model(slug)
        if port is None:
            result = {"ok": False, "error": f"no model available for {slug}"}
        else:
            content = await call_local_completion(port, job["system_prompt"], job["prompt"])
            result = {"ok": True, "content": content}
    except Exception as exc:  # noqa: BLE001 - always produce a result, never leave the backend hanging
        log(f"[queue {slug}] job {job_id} errored: {type(exc).__name__}: {exc}")
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    result_key = f"{MLX_RESULT_KEY_PREFIX}{job_id}"
    await r.rpush(result_key, json.dumps(result))
    await r.expire(result_key, MLX_RESULT_TTL)

    if result.get("ok"):
        asyncio.create_task(_run_shadow_comparison(slug, job, result["content"]))


async def queue_worker_loop():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    log(f"Connected to Redis at {REDIS_URL}, watching {MLX_QUEUE_KEY}")
    while True:
        try:
            item = await r.blpop(MLX_QUEUE_KEY, timeout=1)
        except asyncio.CancelledError:
            await r.aclose()
            raise
        except Exception as exc:  # noqa: BLE001 - keep the loop alive across transient Redis errors
            log(f"[queue] error: {exc!r}")
            await asyncio.sleep(1)
            continue
        if item is None:
            continue
        _, raw = item
        try:
            job = json.loads(raw)
        except json.JSONDecodeError:
            log(f"[queue] dropped unparseable job: {raw[:200]}")
            continue
        asyncio.create_task(handle_marking_job(r, job))


async def main():
    global POOL
    POOL = await asyncpg.create_pool(DATABASE_URL)
    log(f"Connected to {DATABASE_URL}")

    tasks = [
        asyncio.create_task(training_job_loop(POOL)),
        asyncio.create_task(auto_train_loop(POOL)),
        asyncio.create_task(sweep_loop(POOL)),
        asyncio.create_task(queue_worker_loop()),
    ]

    stop_event = asyncio.Event()

    def _handle_signal():
        log("Shutting down...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    await stop_event.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await POOL.close()


if __name__ == "__main__":
    asyncio.run(main())
