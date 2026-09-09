import json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import List, Optional
from dependencies import rate_limit, get_current_admin_id
from database import get_db
import ai_pipeline
import ingestion
import fitz
import marking_prompt

router = APIRouter(dependencies=[Depends(rate_limit), Depends(get_current_admin_id)])

VALID_MARKING_MODEL_STATUSES = {"none", "training", "gated", "live"}

class UpdateQualificationRequest(BaseModel):
    custom_paper_target_marks: Optional[int] = None
    custom_paper_time_limit_minutes: Optional[int] = None
    tiers: Optional[List[str]] = None

class SetMarkingModelStatusRequest(BaseModel):
    exam_board: str
    level: str
    subject: str
    tier: Optional[str] = None
    status: str
    active_adapter_version: Optional[str] = None
    serving_port: Optional[int] = None
    eval_result: Optional[dict] = None

class SetShadowAdapterRequest(BaseModel):
    exam_board: str
    level: str
    subject: str
    tier: Optional[str] = None
    shadow_adapter_version: Optional[str] = None  # None/omitted clears shadow mode for this spec code

class RequestTrainingJobRequest(BaseModel):
    exam_board: str
    level: str
    subject: str
    tier: Optional[str] = None
    base_model: Optional[str] = None

class GradeBoundaryEntry(BaseModel):
    grade: str
    min_pct: float

class SetGradeBoundariesRequest(BaseModel):
    tier: Optional[str] = None
    boundaries: List[GradeBoundaryEntry]

IN_FLIGHT_TRAINING_STATUSES = ('queued', 'assembling', 'training', 'evaluating')
# Must stay in sync with train_lora.py's DEFAULT_MODEL - both fall back to
# this when a training-jobs request omits base_model. Best-known result
# (94% exact-mark-match on aqa-gcse-biology-higher, job 143d9848) came from
# this model; a Qwen2.5-1.5B default briefly crept in undocumented and
# regressed a later retrain's accuracy 94% -> 84%, so pin it explicitly.
DEFAULT_TRAINING_BASE_MODEL = 'mlx-community/Llama-3.2-3B-Instruct-4bit'

QUALIFICATION_LIST_SQL = '''
    SELECT q.id, q.exam_board, q.level, q.subject, q.tiers,
           q.custom_paper_target_marks, q.custom_paper_time_limit_minutes,
           COUNT(p.id) AS paper_count
    FROM qualifications q
    LEFT JOIN papers p
        ON p.exam_board = q.exam_board AND p.level = q.level AND p.subject = q.subject
    {where}
    GROUP BY q.id, q.exam_board, q.level, q.subject, q.tiers,
             q.custom_paper_target_marks, q.custom_paper_time_limit_minutes
'''

@router.get("")
async def list_qualifications(db=Depends(get_db)):
    """
    Backs the admin "Manage Subjects" list page - one row per (exam_board,
    level, subject) qualification, with its custom-paper target marks/time
    limit (NULL if not yet configured, in which case /generate/custom-paper
    falls back to its own heuristic) and a live count of ingested papers, so
    the list reads as "what's actually here" rather than just config.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        rows = await conn.fetch(
            QUALIFICATION_LIST_SQL.format(where="") + " ORDER BY q.exam_board, q.level, q.subject"
        )
    return [dict(r) for r in rows]

# The next two routes (/marking-models) are registered ahead of
# /{qualification_id} below - FastAPI matches path operations in
# registration order, so a literal "/marking-models" would otherwise be
# swallowed by "/{qualification_id}" with qualification_id="marking-models".

@router.get("/marking-models")
async def list_marking_models(db=Depends(get_db)):
    """
    §6.3/§6.4 rollout gating console. One row per (exam_board, level,
    subject, tier) that actually has ingested papers - not just ones
    already gated - joined against any spec_code_marking_models row and a
    live count of the §6.2 training corpus, so an admin can see "this spec
    code has 251 accepted training examples and no model yet" and decide
    whether it's worth training, without cross-referencing training/data/
    by hand.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        rows = await conn.fetch('''
            SELECT
                p.exam_board, p.level, p.subject, COALESCE(p.tier, '') AS tier,
                m.status, m.active_adapter_version, m.active_adapter_prompt_hash,
                m.shadow_adapter_version, m.serving_port, m.eval_result, m.updated_at,
                COUNT(te.id) FILTER (WHERE te.is_accepted_for_training) AS accepted_training_examples
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
            GROUP BY p.exam_board, p.level, p.subject, p.tier,
                     m.status, m.active_adapter_version, m.active_adapter_prompt_hash,
                     m.shadow_adapter_version, m.serving_port, m.eval_result, m.updated_at
            ORDER BY p.exam_board, p.level, p.subject, p.tier
        ''')
    # current_prompt_hash lets an admin see at a glance whether a row's own
    # active_adapter_prompt_hash has since diverged from the live
    # marking_prompt.py, without cross-referencing agent.py's logs - the
    # same comparison training/agent.py::_resolve_adapter makes at serve
    # time (and warns loudly about there), surfaced here too.
    current_hash = marking_prompt.prompt_hash()
    return [
        {**dict(r), "status": r["status"] or "none", "current_prompt_hash": current_hash}
        for r in rows
    ]

@router.put("/marking-models/shadow")
async def set_shadow_adapter(req: SetShadowAdapterRequest, db=Depends(get_db)):
    """
    Configures (or clears, with shadow_adapter_version omitted/null) a
    shadow-mode candidate adapter for a spec code that already has
    status='live'. Once set, training/agent.py dual-runs a sample of real
    incoming marking requests through both the live and candidate adapters
    (see agent.py::_run_shadow_comparison) and logs both outputs to
    shadow_marking_comparisons for offline comparison - the candidate's
    output is NEVER served to a student. This lets a promotion decision be
    based on real traffic shape, not only the held-out eval set, before
    flipping this endpoint's sibling (PUT /marking-models) to make the
    candidate live for real.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        row = await conn.fetchrow('''
            UPDATE spec_code_marking_models
            SET shadow_adapter_version = $5, updated_at = now()
            WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4
            RETURNING exam_board, level, subject, tier, status, active_adapter_version, shadow_adapter_version
        ''', req.exam_board, req.level, req.subject, req.tier or '', req.shadow_adapter_version)
    if not row:
        raise HTTPException(status_code=404, detail="No marking-model row for this spec code - set it live first")
    return dict(row)

@router.get("/marking-models/shadow-comparisons")
async def list_shadow_comparisons(
    exam_board: str, level: str, subject: str, tier: Optional[str] = None,
    limit: int = 200, db=Depends(get_db),
):
    """
    Aggregate + recent-sample view of a spec code's shadow-mode comparison
    log (see set_shadow_adapter above) - the evidence base for deciding
    whether to promote a shadow candidate: how often it agrees with the live
    adapter's mark on the same real request, and how often it violates the
    ebi/www consistency rule on real traffic, not just the held-out set.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    tier = tier or ''
    async with db.acquire() as conn:
        summary = await conn.fetchrow('''
            SELECT
                COUNT(*) AS n_total,
                COUNT(*) FILTER (WHERE marks_agree IS NOT NULL) AS n_comparable,
                COUNT(*) FILTER (WHERE marks_agree = true) AS n_marks_agree,
                COUNT(*) FILTER (WHERE shadow_contradictory = true) AS n_shadow_contradictory,
                MAX(shadow_adapter_version) AS shadow_adapter_version
            FROM shadow_marking_comparisons
            WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4
        ''', exam_board, level, subject, tier)
        recent = await conn.fetch('''
            SELECT live_adapter_version, shadow_adapter_version, live_result, shadow_result,
                   marks_agree, shadow_contradictory, created_at
            FROM shadow_marking_comparisons
            WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4
            ORDER BY created_at DESC LIMIT $5
        ''', exam_board, level, subject, tier, limit)
    n_total = summary["n_total"] or 0
    n_comparable = summary["n_comparable"] or 0
    return {
        "n_total": n_total,
        "n_comparable": n_comparable,
        "marks_agree_rate": (summary["n_marks_agree"] / n_comparable) if n_comparable else None,
        "shadow_contradictory_rate": (summary["n_shadow_contradictory"] / n_total) if n_total else None,
        "shadow_adapter_version": summary["shadow_adapter_version"],
        "recent": [dict(r) for r in recent],
    }

@router.put("/marking-models")
async def set_marking_model_status(req: SetMarkingModelStatusRequest, db=Depends(get_db)):
    """
    Admin-facing gate for §6.3 stage 3 / §6.4: this is the only place that
    flips a spec code's status to 'live', which is what makes
    marking_engine.mark_question start calling the self-hosted model for
    that spec code's 3+ mark questions (routers/feedback.py checks this
    table per submission). Setting eval_result lets an admin attach the
    training/evaluate.py output that justified the status change.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    if req.status not in VALID_MARKING_MODEL_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(VALID_MARKING_MODEL_STATUSES)}")

    async with db.acquire() as conn:
        row = await conn.fetchrow('''
            INSERT INTO spec_code_marking_models
                (exam_board, level, subject, tier, status, active_adapter_version, serving_port, eval_result, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
            ON CONFLICT (exam_board, level, subject, tier) DO UPDATE SET
                status = EXCLUDED.status,
                active_adapter_version = EXCLUDED.active_adapter_version,
                serving_port = EXCLUDED.serving_port,
                eval_result = EXCLUDED.eval_result,
                updated_at = now()
            RETURNING exam_board, level, subject, tier, status, active_adapter_version, serving_port, eval_result, updated_at
        ''',
            req.exam_board, req.level, req.subject, req.tier or '',
            req.status, req.active_adapter_version, req.serving_port,
            json.dumps(req.eval_result) if req.eval_result is not None else None
        )
    return dict(row)

# Same registration-order reasoning as /marking-models above - these two
# literal paths must come before the /{qualification_id} catch-all.

@router.post("/training-jobs")
async def request_training_job(req: RequestTrainingJobRequest, admin_id=Depends(get_current_admin_id), db=Depends(get_db)):
    """
    Enqueues a training run for the Mac-native agent (backend/training/agent.py)
    to pick up - this endpoint never runs the pipeline itself, it just inserts
    a 'queued' row. Refuses a second in-flight request for the same spec code
    (409) since only one training run at a time makes sense on a single GPU,
    and the agent processes strictly one job at a time anyway.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    tier = req.tier or ''
    async with db.acquire() as conn:
        existing = await conn.fetchval('''
            SELECT 1 FROM training_jobs
            WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4
              AND status = ANY($5::text[])
            LIMIT 1
        ''', req.exam_board, req.level, req.subject, tier, list(IN_FLIGHT_TRAINING_STATUSES))
        if existing:
            raise HTTPException(status_code=409, detail="A training job is already in progress for this spec code")

        # Anchors this job's accepted_examples_at_request (§6.5 auto-retrain
        # threshold, agent.py::auto_train_loop) - same aggregation as
        # list_marking_models's accepted_training_examples count above.
        accepted_count = await conn.fetchval('''
            SELECT COUNT(te.id) FILTER (WHERE te.is_accepted_for_training)
            FROM questions q
            JOIN papers p ON p.id = q.paper_id
            LEFT JOIN training_examples te ON te.question_id = q.id
            WHERE p.exam_board = $1 AND p.level = $2 AND p.subject = $3 AND COALESCE(p.tier, '') = $4
        ''', req.exam_board, req.level, req.subject, tier)

        row = await conn.fetchrow('''
            INSERT INTO training_jobs (exam_board, level, subject, tier, base_model, requested_by, accepted_examples_at_request)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
        ''', req.exam_board, req.level, req.subject, tier,
            req.base_model or DEFAULT_TRAINING_BASE_MODEL, admin_id, accepted_count)
    return dict(row)

@router.get("/training-jobs")
async def list_training_jobs(
    exam_board: Optional[str] = None,
    level: Optional[str] = None,
    subject: Optional[str] = None,
    tier: Optional[str] = None,
    db=Depends(get_db),
):
    """Backs the training log/status panel in AdminSubjectDetail - polled while a job is in flight."""
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    clauses, params = [], []
    for name, value in (("exam_board", exam_board), ("level", level), ("subject", subject), ("tier", tier)):
        if value is not None:
            params.append(value)
            clauses.append(f"{name} = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM training_jobs {where} ORDER BY created_at DESC LIMIT 50", *params
        )
    return [dict(r) for r in rows]

@router.post("/grade-boundaries/ingest")
async def ingest_grade_boundaries(
    file: UploadFile = File(...),
    exam_board: str = Form(...),
    level: str = Form(...),
    db=Depends(get_db)
):
    """
    Manage Subjects "..." menu -> "Update grade boundaries": bulk ingestion
    of an exam board's official grade boundaries publication for one level
    (e.g. "all OCR GCSE grade boundaries, June 2025"). One such document
    covers every subject/tier at that level, so this replaces
    set_grade_boundaries's per-subject manual entry with a single upload
    that updates every qualification the document actually mentions.

    Primarily parses the document's real table structure deterministically
    (ingestion.parse_grade_boundaries_table/match_grade_boundary_rows) -
    exact arithmetic on the document's own raw marks, not an LLM's guess at
    reconstructing a large numeric table from flattened text, which is what
    an earlier version of this endpoint did and which produced wrong
    boundaries. Only falls back to that LLM-based text extraction
    (ai_pipeline.extract_grade_boundaries_from_text) when no table rows can
    be parsed at all - e.g. a scanned/image-only PDF with no real table
    layer to read.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Grade boundaries document must be a PDF file")
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    doc_bytes = await file.read()

    async with db.acquire() as conn:
        quals = await conn.fetch(
            'SELECT subject, tiers FROM qualifications WHERE exam_board = $1 AND level = $2',
            exam_board, level
        )
        if not quals:
            raise HTTPException(status_code=404, detail=f"No qualifications found for {exam_board} {level}")

        candidates = [{"subject": q["subject"], "tiers": q["tiers"]} for q in quals]

        table_rows = ingestion.parse_grade_boundaries_table(doc_bytes)
        if table_rows:
            matched, skipped = ingestion.match_grade_boundary_rows(table_rows, candidates)
            extra_matched, skipped = await ingestion.match_unmatched_grade_boundaries(table_rows, candidates, skipped)
            matched += extra_matched
        else:
            doc = fitz.open(stream=doc_bytes, filetype="pdf")
            doc_text = "\n\n".join(doc[i].get_text() for i in range(len(doc)))
            extracted = await ai_pipeline.extract_grade_boundaries_from_text(doc_text, candidates)

            qual_by_subject = {c["subject"].strip().lower(): c for c in candidates}
            matched, skipped = [], []
            for entry in extracted:
                subject_in = entry.get("subject") or ""
                tier_in = (entry.get("tier") or "").strip()
                boundaries = entry.get("boundaries") or []

                qual = qual_by_subject.get(subject_in.strip().lower())
                if not qual:
                    skipped.append({"subject": subject_in, "tier": tier_in, "reason": "subject not recognised"})
                    continue

                grades = [b["grade"] for b in boundaries]
                if len(grades) != len(set(grades)):
                    skipped.append({"subject": qual["subject"], "tier": tier_in, "reason": "duplicate grade labels extracted"})
                    continue

                qual_tiers = qual["tiers"] or []
                if qual_tiers:
                    match = next((t for t in qual_tiers if t.strip().lower() == tier_in.lower()), None)
                    if not match:
                        skipped.append({
                            "subject": qual["subject"], "tier": tier_in,
                            "reason": f"tier not recognised (expected one of {qual_tiers})"
                        })
                        continue
                    tier = match
                else:
                    tier = ''
                matched.append({"subject": qual["subject"], "tier": tier, "boundaries": boundaries})

        if not matched:
            raise HTTPException(status_code=422, detail="Could not match any known subject's grade boundaries in this document")

        updated = []
        for m in matched:
            async with conn.transaction():
                await conn.execute('''
                    DELETE FROM grade_boundaries
                    WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4
                ''', exam_board, level, m["subject"], m["tier"])
                for b in m["boundaries"]:
                    await conn.execute('''
                        INSERT INTO grade_boundaries (exam_board, level, subject, tier, grade, min_pct)
                        VALUES ($1, $2, $3, $4, $5, $6)
                    ''', exam_board, level, m["subject"], m["tier"], b["grade"], b["min_pct"])
            updated.append({"subject": m["subject"], "tier": m["tier"], "grades": len(m["boundaries"])})

    return {"updated": updated, "skipped": skipped}

@router.get("/{qualification_id}/grade-boundaries")
async def list_grade_boundaries(qualification_id: str, db=Depends(get_db)):
    """
    All grade boundary rows for this qualification, across every tier -
    backs the admin editor's per-tier boundary list. Replaces the old
    hardcoded GCSE 9-1 percentage table that analytics.py used to apply to
    every subject/board/tier alike.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        qual = await conn.fetchrow('SELECT exam_board, level, subject FROM qualifications WHERE id = $1', qualification_id)
        if not qual:
            raise HTTPException(status_code=404, detail="Qualification not found")
        rows = await conn.fetch('''
            SELECT tier, grade, min_pct
            FROM grade_boundaries
            WHERE exam_board = $1 AND level = $2 AND subject = $3
            ORDER BY tier, min_pct DESC
        ''', qual['exam_board'], qual['level'], qual['subject'])
    return [dict(r) for r in rows]

@router.put("/{qualification_id}/grade-boundaries")
async def set_grade_boundaries(qualification_id: str, req: SetGradeBoundariesRequest, db=Depends(get_db)):
    """
    Replaces the full boundary set for one tier of this qualification
    (delete + reinsert, so removing a grade from the admin editor actually
    removes its row rather than leaving it stale). tier '' means untiered.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    tier = (req.tier or '').strip()
    grades = [b.grade.strip() for b in req.boundaries]
    if len(grades) != len(set(grades)):
        raise HTTPException(status_code=400, detail="Grade labels must be unique within a tier")
    if any(not g for g in grades):
        raise HTTPException(status_code=400, detail="Grade labels cannot be blank")

    async with db.acquire() as conn:
        qual = await conn.fetchrow('SELECT exam_board, level, subject FROM qualifications WHERE id = $1', qualification_id)
        if not qual:
            raise HTTPException(status_code=404, detail="Qualification not found")

        async with conn.transaction():
            await conn.execute('''
                DELETE FROM grade_boundaries
                WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4
            ''', qual['exam_board'], qual['level'], qual['subject'], tier)
            for b in req.boundaries:
                await conn.execute('''
                    INSERT INTO grade_boundaries (exam_board, level, subject, tier, grade, min_pct)
                    VALUES ($1, $2, $3, $4, $5, $6)
                ''', qual['exam_board'], qual['level'], qual['subject'], tier, b.grade.strip(), b.min_pct)

        rows = await conn.fetch('''
            SELECT tier, grade, min_pct FROM grade_boundaries
            WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4
            ORDER BY min_pct DESC
        ''', qual['exam_board'], qual['level'], qual['subject'], tier)
    return [dict(r) for r in rows]

@router.get("/{qualification_id}")
async def get_qualification(qualification_id: str, db=Depends(get_db)):
    """Backs the dedicated per-subject admin page (single-row lookup by id)."""
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            QUALIFICATION_LIST_SQL.format(where="WHERE q.id = $1"), qualification_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Qualification not found")
    return dict(row)

@router.put("/{qualification_id}")
async def update_qualification(qualification_id: str, req: UpdateQualificationRequest, db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    if req.custom_paper_target_marks is not None and req.custom_paper_target_marks <= 0:
        raise HTTPException(status_code=400, detail="Target marks must be a positive number")
    if req.custom_paper_time_limit_minutes is not None and req.custom_paper_time_limit_minutes <= 0:
        raise HTTPException(status_code=400, detail="Time limit must be a positive number of minutes")

    async with db.acquire() as conn:
        existing = await conn.fetchrow('SELECT tiers FROM qualifications WHERE id = $1', qualification_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Qualification not found")

        if req.tiers is not None:
            tiers = [t.strip() for t in req.tiers if t.strip()]
            if not tiers:
                raise HTTPException(status_code=400, detail="At least one tier is required")
        else:
            tiers = existing['tiers']

        row = await conn.fetchrow('''
            UPDATE qualifications
            SET custom_paper_target_marks = $1, custom_paper_time_limit_minutes = $2, tiers = $3
            WHERE id = $4
            RETURNING id, exam_board, level, subject, tiers,
                      custom_paper_target_marks, custom_paper_time_limit_minutes
        ''', req.custom_paper_target_marks, req.custom_paper_time_limit_minutes, tiers, qualification_id)

    return dict(row)

@router.delete("/{qualification_id}")
async def delete_qualification(qualification_id: str, db=Depends(get_db)):
    """
    Removes a qualification from the admin-facing lists (Manage Subjects,
    the ingestion upload form's qualification picker, student onboarding).

    qualifications only holds config for a (exam_board, level, subject)
    combination (tiers, custom paper marks/time) - spec_topics, papers,
    questions and user_subjects are matched by those same string fields,
    not a foreign key to this table, so deleting a qualification does NOT
    delete any already-ingested topics/papers/questions; it just stops that
    combination from being offered as a qualification to add new content
    against or onboard into. Re-adding the subject (Manage Subjects "Add
    subject") recreates the row and re-links straight back to that existing
    content, since spec_topics/papers keyed on the same strings.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        deleted_id = await conn.fetchval('DELETE FROM qualifications WHERE id = $1 RETURNING id', qualification_id)
    if not deleted_id:
        raise HTTPException(status_code=404, detail="Qualification not found")
    return {"status": "deleted", "id": qualification_id}
