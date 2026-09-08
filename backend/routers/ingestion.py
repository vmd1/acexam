from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from dependencies import rate_limit, get_current_admin_id
from database import get_db
import ingestion
import ai_pipeline
import storage
import asyncpg
import fitz
import json
import hashlib

router = APIRouter(dependencies=[Depends(rate_limit), Depends(get_current_admin_id)])

# Real past-paper/spec/mark-scheme/examiner-report PDFs (AQA/Edexcel/OCR)
# don't come close to this in practice - 50MB is generous headroom for even
# a scan-heavy paper while still bounding the memory, PDF-parsing CPU, and
# LLM-cost blast radius of a single upload (defense-in-depth: these
# endpoints are already admin-only). See vmd1/acexam#19.
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024

class UpdateQuestionRequest(BaseModel):
    question_number: Optional[str] = None
    mark_value: Optional[int] = None
    question_text: Optional[str] = None
    marking_type: Optional[str] = None
    marking_dsl: Optional[str] = None
    mark_scheme_text: Optional[str] = None
    needs_review: Optional[bool] = None
    images: Optional[List[Dict[str, Any]]] = None

class ApproveMisconceptionRequest(BaseModel):
    spec_code: str
    tag_id: str

class ApproveMisconceptionEntry(BaseModel):
    spec_code: str
    tag_id: str

class BulkApproveMisconceptionsRequest(BaseModel):
    tags: List[ApproveMisconceptionEntry]

@router.post("/upload")
async def upload_paper(
    file: UploadFile = File(...),
    mark_scheme_file: Optional[UploadFile] = File(None),
    examiner_report_file: Optional[UploadFile] = File(None),
    exam_board: str = Form(...),
    subject: str = Form(...),
    level: str = Form(...),
    tier: Optional[str] = Form(None),
    user_id: str = Depends(get_current_admin_id),
    db=Depends(get_db)
):
    """
    The admin only picks the qualification (exam board/level/subject - an
    existing spec_topics combo, so questions can be classified against real
    topics) and, if that qualification is tiered, which tier this specific
    paper is. Everything else - paper code, series, and each question's own
    content/topic/marking/misconceptions - is read off the PDFs themselves,
    the same way spec ingestion derives its own metadata rather than relying
    on admin-typed fields.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Question paper must be a PDF file")

    qp_bytes = await file.read()
    if len(qp_bytes) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    ms_bytes = (await mark_scheme_file.read()) if mark_scheme_file else None
    if ms_bytes is not None and len(ms_bytes) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    er_bytes = (await examiner_report_file.read()) if examiner_report_file else None
    if er_bytes is not None and len(er_bytes) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    # Read the paper's own code and series/session off its cover page,
    # rather than an admin-facing form field - a real exam paper states
    # these outright, the same way it states topics/questions.
    paper_code, series = None, None
    try:
        qp_doc = fitz.open(stream=qp_bytes, filetype="pdf")
        cover_text = "\n\n".join(qp_doc[i].get_text() for i in range(min(2, len(qp_doc))))
        cover_meta = await ai_pipeline.extract_paper_cover_metadata_from_text(cover_text)
        paper_code, series = cover_meta.get("paper_code"), cover_meta.get("series")
    except Exception as e:
        print(f"Paper cover metadata detection failed: {type(e).__name__}: {e}")

    # Fetch this subject's known specification topics so the AI can classify
    # each question (and any scanned misconceptions) against the real topic
    # list instead of everything in the paper being tagged with one
    # manually-typed spec_code.
    async with db.acquire() as conn:
        known_topic_rows = await conn.fetch(
            'SELECT spec_code, title FROM spec_topics WHERE exam_board = $1 AND subject = $2 AND level = $3',
            exam_board, subject, level
        )
    known_topics = [{"spec_code": r["spec_code"], "title": r["title"]} for r in known_topic_rows]

    # Misconception taxonomy in scope for this qualification (§6.2a). Two
    # different views of the same table, for two different jobs:
    # - known_misconceptions (approved only): the independent grader (§6.2)
    #   may only *attach* one of these to a training example - never invent
    #   its own phrasing there, since an unapproved tag isn't trustworthy
    #   enough to label training data with yet.
    # - existing_taxonomy (approved + pending): grounds both the grader's
    #   new_tag_suggestion and the mark-scheme/examiner-report scanner so
    #   neither re-proposes a misconception that's already been surfaced,
    #   even while still pending approval.
    # A brand-new spec code with nothing in either list yet is fine - it
    # just means nothing gets attached/deduped against, not that anything
    # fails.
    known_codes = [t["spec_code"] for t in known_topics]
    known_misconceptions = []
    existing_taxonomy = []
    if known_codes:
        async with db.acquire() as conn:
            taxonomy_rows = await conn.fetch(
                'SELECT tag_id, label, description, spec_code, approved_at FROM misconception_taxonomy WHERE spec_code = ANY($1)',
                known_codes
            )
        existing_taxonomy = [
            {"tag_id": r["tag_id"], "label": r["label"], "description": r["description"], "spec_code": r["spec_code"]}
            for r in taxonomy_rows
        ]
        known_misconceptions = [
            {"tag_id": r["tag_id"], "label": r["label"]}
            for r in taxonomy_rows if r["approved_at"] is not None
        ]

    try:
        results = await ingestion.run_full_ai_ingestion_pipeline(
            question_paper_bytes=qp_bytes,
            mark_scheme_bytes=ms_bytes,
            examiner_report_bytes=er_bytes,
            exam_board=exam_board,
            subject=subject,
            known_topics=known_topics,
            known_misconceptions=known_misconceptions,
            existing_taxonomy=existing_taxonomy
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Ingestion Pipeline failed: {str(e)}")

    # Real object storage (MinIO locally, any S3-compatible service in prod)
    # for the original uploaded PDF - checksum-keyed so re-uploading the
    # identical file is a no-op rather than a new object every time.
    source_pdf_url = await storage.upload_bytes(
        f"papers/{hashlib.sha256(qp_bytes).hexdigest()}.pdf", qp_bytes, "application/pdf"
    )

    async with db.acquire() as conn:
        # 1. Create Paper Record
        paper_id = await conn.fetchval('''
            INSERT INTO papers (exam_board, subject, paper_code, series, source_pdf_url, status, uploaded_by, level, tier, ingestion_token_usage)
            VALUES ($1, $2, $3, $4, $5, 'needs_review', $6, $7, $8, $9)
            RETURNING id
        ''', exam_board, subject, paper_code, series, source_pdf_url, user_id, level, tier, json.dumps(results.get("token_usage", {})))

        # 2. Resolve each known topic's spec_code to its id, for per-question classification
        topic_id_by_code = {r["spec_code"]: r["id"] for r in await conn.fetch(
            'SELECT id, spec_code FROM spec_topics WHERE exam_board = $1 AND subject = $2 AND level = $3',
            exam_board, subject, level
        )}

        # 3. Insert Extracted Questions. A question can genuinely test more
        # than one spec topic (see ingestion.py's topic_spec_codes) - every
        # code the AI splitter confidently matched gets resolved to an id and
        # linked via question_topics below, while spec_topic_id keeps the
        # FIRST/primary match for every existing single-topic consumer
        # (mastery tracking, analytics grouping, topic name display). A
        # question with no confident match at all is left uncategorized
        # (spec_topic_id NULL, no question_topics rows) rather than dumped
        # under one manually-picked fallback topic - needs_review already
        # flags it for admin attention. Maps a question's own paper-scoped
        # number (e.g. "3(b)(ii)") to its freshly-assigned id, so the
        # synthetic training examples generated per-question by the pipeline
        # (step 7 below) can be linked to a question_id that didn't exist
        # until this insert ran.
        question_id_by_number: dict[str, str] = {}
        for q in results.get("questions", []):
            topic_ids = [
                topic_id_by_code[code] for code in (q.get("topic_spec_codes") or [])
                if code in topic_id_by_code
            ]
            primary_topic_id = topic_ids[0] if topic_ids else None
            question_id = await conn.fetchval('''
                INSERT INTO questions (
                    paper_id, question_number, mark_value, question_text,
                    images, marking_type, marking_dsl, mark_scheme_text,
                    needs_review, spec_topic_id, answer_type, answer_options, table_data
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                RETURNING id
            ''',
                paper_id, q["question_number"], q["mark_value"], q["question_text"],
                json.dumps(q.get("images", [])), q["marking_type"], q.get("marking_dsl"),
                q.get("mark_scheme_text"), q.get("needs_review", False), primary_topic_id,
                q.get("answer_type", "written"),
                json.dumps(q["answer_options"]) if q.get("answer_options") is not None else None,
                json.dumps(q["table_data"]) if q.get("table_data") is not None else None
            )
            question_id_by_number[q["question_number"]] = question_id
            if topic_ids:
                await conn.executemany('''
                    INSERT INTO question_topics (question_id, spec_topic_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                ''', [(question_id, tid) for tid in topic_ids])

        # 4. Save Proposed Misconceptions (§6.2a). Each is already classified
        # against a known topic by the scanner; misconception_taxonomy.spec_code
        # is NOT NULL so any that couldn't be matched were already dropped.
        for pm in results.get("proposed_misconceptions", []):
            await conn.execute('''
                INSERT INTO misconception_taxonomy (spec_code, tag_id, label, description, approved_at)
                VALUES ($1, $2, $3, $4, NULL)
                ON CONFLICT (spec_code, tag_id) DO NOTHING
            ''', pm.get("spec_code"), pm.get("tag_id"), pm.get("label"), pm.get("description"))

        # 5. Persist Synthetic Training Examples (§6.2). Generated per-question
        # by the pipeline before any id existed; resolved here via the number
        # map built above. An example whose question failed to insert (should
        # not happen, but the map lookup is the guard) is silently skipped
        # rather than raising, so one bad number can't fail the whole upload.
        for te in results.get("synthetic_training_dataset", []):
            question_id = question_id_by_number.get(te.get("question_number"))
            if not question_id:
                continue
            await conn.execute('''
                INSERT INTO training_examples (
                    question_id, spec_code, candidate_answer, target_marks,
                    awarded_marks, is_accepted_for_training, www,
                    missed_points, misconception_tags, source
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ''',
                question_id, te.get("spec_code") or None, te.get("student_answer", ""),
                te.get("target_marks", 0), te.get("awarded_marks", 0),
                bool(te.get("is_accepted_for_training", False)),
                json.dumps(te.get("www", [])),
                json.dumps(te.get("missed_points", [])),
                json.dumps(te.get("misconception_tags", [])),
                te.get("source", "synthetic")
            )

    return {
        "message": "AI Pipeline completed successfully. Paper queued for admin review.",
        "paper_id": str(paper_id),
        "filename": file.filename,
        "extracted_visuals_count": len(results.get("images", [])),
        "extracted_tables_count": len(results.get("tables", [])),
        "questions_extracted": len(results.get("questions", [])),
        "proposed_misconceptions_count": len(results.get("proposed_misconceptions", [])),
        "synthetic_training_examples_count": len(results.get("synthetic_training_dataset", [])),
        "token_usage": results.get("token_usage", {})
    }

@router.post("/upload-spec")
async def upload_specification(
    file: UploadFile = File(...),
    exam_board: str = Form(...),
    subject: str = Form(...),
    level: str = Form(...),
    db=Depends(get_db)
):
    """
    Creates/updates a qualification (§ Manage Subjects "Add subject") and
    pre-populates its spec_topics from the official exam board specification
    document, so the full topic hierarchy exists before any past paper is
    ingested (rather than admins hand-typing one spec_code per paper upload).

    Exam board, subject and level are admin-supplied (the identity of the
    qualification being added/updated) rather than parsed off the document's
    own cover page/branding - relying on a PDF's own formatting for this was
    fragile (mismatches would silently misfile topics under the wrong
    qualification). Only the topic hierarchy and tiers are derived from the
    document text itself, since those aren't practical to hand-type.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Specification must be a PDF file")
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    spec_bytes = await file.read()
    if len(spec_bytes) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    doc = fitz.open(stream=spec_bytes, filetype="pdf")
    spec_text = "\n\n".join(doc[i].get_text() for i in range(len(doc)))

    result = await ai_pipeline.extract_spec_topics_from_text(spec_text)
    topics = result["topics"]
    tiers = result["tiers"]
    target_marks = result.get("target_marks")
    time_limit_minutes = result.get("time_limit_minutes")
    if not topics:
        raise HTTPException(status_code=422, detail="Could not extract any topics from this specification document")

    async with db.acquire() as conn:
        code_to_id = {}
        for t in topics:
            topic_id = await conn.fetchval('''
                INSERT INTO spec_topics (exam_board, subject, level, spec_code, title, tier_only)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (exam_board, subject, level, spec_code)
                DO UPDATE SET title = EXCLUDED.title, tier_only = EXCLUDED.tier_only
                RETURNING id
            ''', exam_board, subject, level, t["spec_code"], t["title"], t.get("tier_only"))
            code_to_id[t["spec_code"]] = topic_id

        for t in topics:
            if t.get("parent_spec_code") and t["parent_spec_code"] in code_to_id:
                await conn.execute(
                    'UPDATE spec_topics SET parent_id = $1 WHERE id = $2',
                    code_to_id[t["parent_spec_code"]], code_to_id[t["spec_code"]]
                )

        # Record this qualification's tiers (e.g. Higher/Foundation), as
        # stated by the specification itself - drives the tier step in the
        # frontend subject picker. Empty tiers = no tier step (e.g. A-Level).
        # target_marks/time_limit_minutes (Manage Subjects "Custom paper
        # settings") are pre-filled from the same document's "Scheme of
        # assessment" section when extractable - but only ever fill a still-
        # NULL value (COALESCE keeps whatever's already stored), so
        # re-uploading a spec to refresh topics never silently overwrites an
        # admin's own deliberate edit to these two fields.
        await conn.execute('''
            INSERT INTO qualifications (exam_board, level, subject, tiers, custom_paper_target_marks, custom_paper_time_limit_minutes)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (exam_board, level, subject) DO UPDATE SET
                tiers = EXCLUDED.tiers,
                custom_paper_target_marks = COALESCE(qualifications.custom_paper_target_marks, EXCLUDED.custom_paper_target_marks),
                custom_paper_time_limit_minutes = COALESCE(qualifications.custom_paper_time_limit_minutes, EXCLUDED.custom_paper_time_limit_minutes)
        ''', exam_board, level, subject, tiers, target_marks, time_limit_minutes)

    return {
        "message": "Specification parsed and topics created.",
        "topics_created": len(topics),
        "exam_board": exam_board,
        "subject": subject,
        "level": level,
        "target_marks": target_marks,
        "time_limit_minutes": time_limit_minutes,
    }

@router.get("/papers")
async def list_admin_papers(db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        rows = await conn.fetch('''
            SELECT p.*, COUNT(q.id) as question_count
            FROM papers p
            LEFT JOIN questions q ON p.id = q.paper_id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        ''')
        return [dict(r) for r in rows]

@router.get("/paper/{paper_id}")
async def get_admin_paper_detail(paper_id: str, db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        paper = await conn.fetchrow('SELECT * FROM papers WHERE id = $1', paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found")
        questions = await conn.fetch('''
            SELECT * FROM questions WHERE paper_id = $1 ORDER BY created_at ASC
        ''', paper_id)
        return {
            "paper": dict(paper),
            "questions": [dict(q) for q in questions]
        }

@router.put("/question/{question_id}")
async def update_admin_question(
    question_id: str,
    req: UpdateQuestionRequest,
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        fields = []
        params = [question_id]
        if req.question_number is not None:
            params.append(req.question_number)
            fields.append(f"question_number = ${len(params)}")
        if req.mark_value is not None:
            params.append(req.mark_value)
            fields.append(f"mark_value = ${len(params)}")
        if req.question_text is not None:
            params.append(req.question_text)
            fields.append(f"question_text = ${len(params)}")
        if req.marking_type is not None:
            params.append(req.marking_type)
            fields.append(f"marking_type = ${len(params)}")
        if req.marking_dsl is not None:
            params.append(req.marking_dsl)
            fields.append(f"marking_dsl = ${len(params)}")
        if req.mark_scheme_text is not None:
            params.append(req.mark_scheme_text)
            fields.append(f"mark_scheme_text = ${len(params)}")
        if req.needs_review is not None:
            params.append(req.needs_review)
            fields.append(f"needs_review = ${len(params)}")
        if req.images is not None:
            # Admin-facing removal of a mis-cropped/wrong/duplicate image
            # (§3.1's re-cropping workflow only covers re-cropping, not
            # deletion) - the full replacement array, not a single id, since
            # that's what the edit form already holds after a client-side
            # removal.
            params.append(json.dumps(req.images))
            fields.append(f"images = ${len(params)}::jsonb")

        if fields:
            query = f"UPDATE questions SET {', '.join(fields)} WHERE id = $1"
            await conn.execute(query, *params)
        return {"status": "success", "message": "Question updated"}

@router.post("/paper/{paper_id}/publish")
async def publish_paper(paper_id: str, db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        await conn.execute("UPDATE papers SET status = 'published' WHERE id = $1", paper_id)
        return {"status": "success", "message": "Paper published to live student question bank"}

@router.delete("/paper/{paper_id}")
async def delete_paper(paper_id: str, db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        try:
            result = await conn.execute('DELETE FROM papers WHERE id = $1', paper_id)
        except asyncpg.ForeignKeyViolationError:
            # Students have already attempted/answered questions from this
            # paper (answers/attempts reference it with NO ACTION, not
            # CASCADE, so their work is never silently destroyed) - deleting
            # isn't safe, so surface that instead of failing opaquely.
            raise HTTPException(
                status_code=409,
                detail="Can't delete: students have already attempted questions from this paper."
            )
        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Paper not found")
        return {"status": "success", "message": "Paper deleted"}

@router.get("/misconceptions")
async def list_misconceptions(
    exam_board: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    subject: Optional[str] = Query(None),
    db=Depends(get_db)
):
    """
    misconception_taxonomy only stores spec_code (the fine-grained topic
    point, e.g. '4.2.2.1') - not exam_board/level/subject directly - so
    scoping this to one subject's admin page means joining through
    spec_topics, the table that actually owns that (exam_board, level,
    subject, spec_code) scoping (idx_spec_topics_code). Omitting all three
    filters returns the full unscoped taxonomy, same as before.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        if exam_board and level and subject:
            rows = await conn.fetch('''
                SELECT DISTINCT mt.*
                FROM misconception_taxonomy mt
                JOIN spec_topics st
                    ON st.spec_code = mt.spec_code
                    AND st.exam_board = $1 AND st.level = $2 AND st.subject = $3
                ORDER BY mt.approved_at DESC NULLS LAST, mt.created_at DESC
            ''', exam_board, level, subject)
        else:
            rows = await conn.fetch("SELECT * FROM misconception_taxonomy ORDER BY approved_at DESC NULLS LAST, created_at DESC")
        return [dict(r) for r in rows]

@router.post("/misconception/approve-all")
async def approve_all_misconceptions(req: BulkApproveMisconceptionsRequest, db=Depends(get_db)):
    """
    SubjectMisconceptions "Approve all" - one round trip for every pending
    tag instead of the frontend firing a separate approve request per tag
    (which was both slower and the kind of burst that trips the per-user
    rate limit).
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    if not req.tags:
        return {"status": "success", "message": "No tags to approve", "approved": 0}
    async with db.acquire() as conn:
        await conn.executemany('''
            UPDATE misconception_taxonomy
            SET approved_at = now()
            WHERE spec_code = $1 AND tag_id = $2
        ''', [(t.spec_code, t.tag_id) for t in req.tags])
    return {"status": "success", "message": f"Approved {len(req.tags)} misconception tags", "approved": len(req.tags)}

@router.post("/misconception/approve")
async def approve_misconception(req: ApproveMisconceptionRequest, db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        await conn.execute('''
            UPDATE misconception_taxonomy
            SET approved_at = now()
            WHERE spec_code = $1 AND tag_id = $2
        ''', req.spec_code, req.tag_id)
        return {"status": "success", "message": "Misconception tag approved"}
