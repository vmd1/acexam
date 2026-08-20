from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from database import get_db
from dependencies import rate_limit, get_current_user_id
from profile_engine import calculate_decayed_mastery_scores

router = APIRouter(dependencies=[Depends(rate_limit)])

class CustomPaperRequest(BaseModel):
    subject: Optional[str] = "Biology"
    exam_board: Optional[str] = "AQA"
    spec_topic_ids: Optional[List[str]] = []
    target_marks: Optional[int] = 20

@router.get("/adaptive-queue")
async def get_adaptive_queue(
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
        
    async with db.acquire() as conn:
        # 1. Update memory decay scores
        await calculate_decayed_mastery_scores(conn, user_id)
        
        # 2. Check active misconceptions
        active_misconceptions = await conn.fetch('''
            SELECT spec_code, tag_id, occurrences
            FROM student_misconceptions
            WHERE user_id = $1 AND status = 'active'
            ORDER BY occurrences DESC
            LIMIT 3
        ''', user_id)
        
        # 3. Check lowest mastery / decaying topics
        weak_topics = await conn.fetch('''
            SELECT spec_topic_id, decay_score, mastery_score
            FROM student_topic_mastery
            WHERE user_id = $1
            ORDER BY decay_score ASC
            LIMIT 3
        ''', user_id)
        
        # 4. Check weakest command words
        weak_commands = await conn.fetch('''
            SELECT command_word,
                   CASE WHEN marks_possible > 0 THEN (marks_awarded::float / marks_possible) ELSE 0.0 END as acc
            FROM student_command_word_mastery
            WHERE user_id = $1 AND marks_possible >= 3
            ORDER BY acc ASC
            LIMIT 2
        ''', user_id)
        
        target_topic_ids = [str(r['spec_topic_id']) for r in weak_topics]
        target_spec_codes = [r['spec_code'] for r in active_misconceptions]
        
        # Fetch matching questions or fallback to general bank
        query = '''
            SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
            FROM questions q
            JOIN papers p ON q.paper_id = p.id
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
        '''
        
        conditions = []
        params = []
        if target_topic_ids:
            params.append(target_topic_ids)
            conditions.append(f"q.spec_topic_id = ANY(${len(params)})")
            
        if conditions:
            query += " WHERE " + " OR ".join(conditions)
            
        query += " ORDER BY RANDOM() LIMIT 10"
        
        questions = await conn.fetch(query, *params)
        
        # If not enough questions matched, backfill from general pool
        if len(questions) < 5:
            fallback = await conn.fetch('''
                SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
                FROM questions q
                JOIN papers p ON q.paper_id = p.id
                LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
                ORDER BY RANDOM()
                LIMIT 10
            ''')
            questions = list({q['id']: q for q in (list(questions) + list(fallback))}.values())[:10]
            
        return {
            "queue": [dict(q) for q in questions],
            "focus_reason": {
                "active_misconceptions_count": len(active_misconceptions),
                "weak_topics_count": len(weak_topics),
                "weakest_command_words": [r['command_word'] for r in weak_commands]
            }
        }

@router.post("/custom-paper")
async def generate_custom_paper(
    req: CustomPaperRequest,
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
        
    async with db.acquire() as conn:
        questions = await conn.fetch('''
            SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
            FROM questions q
            JOIN papers p ON q.paper_id = p.id
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            WHERE p.subject = $1
            ORDER BY RANDOM()
            LIMIT 10
        ''', req.subject)
        
        attempt_id = await conn.fetchval('''
            INSERT INTO attempts (user_id, paper_id, source)
            VALUES ($1, NULL, 'custom_generated')
            RETURNING id
        ''', user_id)
        
        return {
            "attempt_id": str(attempt_id),
            "title": f"Custom {req.subject} Mock Paper",
            "questions": [dict(q) for q in questions]
        }
