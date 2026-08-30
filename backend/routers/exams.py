from fastapi import APIRouter, Depends, HTTPException
from database import get_db
from dependencies import rate_limit, get_current_user_id

router = APIRouter(dependencies=[Depends(rate_limit)])

@router.get("/papers")
async def list_papers(db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        rows = await conn.fetch('''
            SELECT p.id, p.exam_board, p.subject, p.paper_code, p.series, p.status, p.created_at,
                   COUNT(q.id) as question_count,
                   COALESCE(SUM(q.mark_value), 0) as total_marks
            FROM papers p
            LEFT JOIN questions q ON p.id = q.paper_id
            WHERE p.status = 'published'
            GROUP BY p.id
            ORDER BY p.created_at DESC
        ''')
        return [dict(r) for r in rows]

@router.get("/paper/{paper_id}")
async def get_paper(paper_id: str, db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        paper = await conn.fetchrow("SELECT * FROM papers WHERE id = $1 AND status = 'published'", paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found")
        questions = await conn.fetch('''
            SELECT q.*, st.spec_code, st.title as topic_title
            FROM questions q
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            WHERE q.paper_id = $1
            ORDER BY q.created_at ASC
        ''', paper_id)
        return {
            "paper": dict(paper),
            "questions": [dict(q) for q in questions]
        }

@router.get("/question/{question_id}")
async def get_question(question_id: str, db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        q = await conn.fetchrow('''
            SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
            FROM questions q
            JOIN papers p ON q.paper_id = p.id
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            WHERE q.id = $1 AND p.status = 'published'
        ''', question_id)
        if not q:
            raise HTTPException(status_code=404, detail="Question not found")
        return dict(q)

@router.get("/topics")
async def get_topics(db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM spec_topics ORDER BY spec_code ASC')
        return [dict(r) for r in rows]

@router.get("/qualifications")
async def get_qualifications(db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        rows = await conn.fetch('SELECT exam_board, level, subject, tiers FROM qualifications')
        return [dict(r) for r in rows]
