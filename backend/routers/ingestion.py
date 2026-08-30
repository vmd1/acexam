from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List
from dependencies import rate_limit, get_current_admin_id
from database import get_db
import ingestion
import ai_pipeline
import asyncpg
import fitz
import json

router = APIRouter(dependencies=[Depends(rate_limit), Depends(get_current_admin_id)])

class UpdateQuestionRequest(BaseModel):
    question_number: Optional[str] = None
    mark_value: Optional[int] = None
    question_text: Optional[str] = None
    marking_type: Optional[str] = None
    marking_dsl: Optional[str] = None
    mark_scheme_text: Optional[str] = None
    needs_review: Optional[bool] = None

class ApproveMisconceptionRequest(BaseModel):
    spec_code: str
    tag_id: str

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
    ms_bytes = (await mark_scheme_file.read()) if mark_scheme_file else None
    er_bytes = (await examiner_report_file.read()) if examiner_report_file else None

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

    try:
        results = await ingestion.run_full_ai_ingestion_pipeline(
            question_paper_bytes=qp_bytes,
            mark_scheme_bytes=ms_bytes,
            examiner_report_bytes=er_bytes,
            exam_board=exam_board,
            subject=subject,
            known_topics=known_topics
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Ingestion Pipeline failed: {str(e)}")

    mock_s3_url = f"https://cdn.acexam.app/papers/{file.filename}"

    async with db.acquire() as conn:
        # 1. Create Paper Record
        paper_id = await conn.fetchval('''
            INSERT INTO papers (exam_board, subject, paper_code, series, source_pdf_url, status, uploaded_by, level, tier)
            VALUES ($1, $2, $3, $4, $5, 'needs_review', $6, $7, $8)
            RETURNING id
        ''', exam_board, subject, paper_code, series, mock_s3_url, user_id, level, tier)

        # 2. Resolve each known topic's spec_code to its id, for per-question classification
        topic_id_by_code = {r["spec_code"]: r["id"] for r in await conn.fetch(
            'SELECT id, spec_code FROM spec_topics WHERE exam_board = $1 AND subject = $2 AND level = $3',
            exam_board, subject, level
        )}

        # 3. Insert Extracted Questions. A question the AI couldn't
        # confidently classify against a known topic is left uncategorized
        # (spec_topic_id NULL) rather than dumped under one manually-picked
        # fallback topic - needs_review already flags it for admin attention.
        for q in results.get("questions", []):
            question_topic_id = topic_id_by_code.get(q.get("topic_spec_code"))
            await conn.execute('''
                INSERT INTO questions (
                    paper_id, question_number, mark_value, question_text,
                    images, marking_type, marking_dsl, mark_scheme_text,
                    needs_review, spec_topic_id, answer_type, answer_options
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ''',
                paper_id, q["question_number"], q["mark_value"], q["question_text"],
                json.dumps(q.get("images", [])), q["marking_type"], q.get("marking_dsl"),
                q.get("mark_scheme_text"), q.get("needs_review", False), question_topic_id,
                q.get("answer_type", "written"),
                json.dumps(q["answer_options"]) if q.get("answer_options") is not None else None
            )

        # 4. Save Proposed Misconceptions (§6.2a). Each is already classified
        # against a known topic by the scanner; misconception_taxonomy.spec_code
        # is NOT NULL so any that couldn't be matched were already dropped.
        for pm in results.get("proposed_misconceptions", []):
            await conn.execute('''
                INSERT INTO misconception_taxonomy (spec_code, tag_id, label, description, approved_at)
                VALUES ($1, $2, $3, $4, NULL)
                ON CONFLICT (spec_code, tag_id) DO NOTHING
            ''', pm.get("spec_code"), pm.get("tag_id"), pm.get("label"), pm.get("description"))

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
    db=Depends(get_db)
):
    """
    Pre-populates spec_topics from the official exam board specification
    document, so the full topic hierarchy exists before any past paper is
    ingested (rather than admins hand-typing one spec_code per paper upload).

    Exam board, subject and level are read off the document itself (its
    cover page/branding) rather than admin-selected - the spec PDF already
    states these, so asking the admin to also pick them from a dropdown is
    redundant and error-prone (mismatches would silently misfile topics).
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Specification must be a PDF file")
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    spec_bytes = await file.read()
    doc = fitz.open(stream=spec_bytes, filetype="pdf")
    spec_text = "\n\n".join(doc[i].get_text() for i in range(len(doc)))

    result = await ai_pipeline.extract_spec_topics_from_text(spec_text)
    topics = result["topics"]
    tiers = result["tiers"]
    if not topics:
        raise HTTPException(status_code=422, detail="Could not extract any topics from this specification document")

    exam_board = result.get("exam_board")
    subject = result.get("subject")
    level = result.get("level")
    if not exam_board or not subject or not level:
        raise HTTPException(
            status_code=422,
            detail="Could not determine exam board, subject and level from this specification document"
        )

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
        await conn.execute('''
            INSERT INTO qualifications (exam_board, level, subject, tiers)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (exam_board, level, subject) DO UPDATE SET tiers = EXCLUDED.tiers
        ''', exam_board, level, subject, tiers)

    return {
        "message": "Specification parsed and topics created.",
        "topics_created": len(topics),
        "exam_board": exam_board,
        "subject": subject,
        "level": level,
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
async def list_misconceptions(db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM misconception_taxonomy ORDER BY approved_at DESC NULLS LAST, created_at DESC")
        return [dict(r) for r in rows]

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
