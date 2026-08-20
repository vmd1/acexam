from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from database import get_db
from dependencies import rate_limit, get_current_user_id
from marking_engine import mark_question
from profile_engine import update_student_profile_after_answer

router = APIRouter(dependencies=[Depends(rate_limit)])

class SubmitAnswerRequest(BaseModel):
    question_id: str
    attempt_id: Optional[str] = None
    answer_text: str
    answer_image_url: Optional[str] = None
    command_word: Optional[str] = None

@router.post("/submit")
async def submit_answer(
    req: SubmitAnswerRequest,
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
        
    async with db.acquire() as conn:
        q = await conn.fetchrow('''
            SELECT q.*, st.spec_code
            FROM questions q
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            WHERE q.id = $1
        ''', req.question_id)
        
        if not q:
            raise HTTPException(status_code=404, detail="Question not found")
            
        # Detect command word if not passed
        command_word = req.command_word
        if not command_word:
            first_word = q['question_text'].strip().split()[0] if q['question_text'] else "Explain"
            command_word = first_word.capitalize()
            
        # Evaluate student answer
        marking_result = await mark_question(
            question_text=q['question_text'],
            mark_value=q['mark_value'],
            marking_type=q['marking_type'],
            marking_dsl=q['marking_dsl'],
            mark_scheme_text=q['mark_scheme_text'],
            student_answer=req.answer_text,
            command_word=command_word,
            spec_code=q['spec_code']
        )
        
        # Ensure attempt exists
        attempt_id = req.attempt_id
        if not attempt_id:
            attempt_id = await conn.fetchval('''
                INSERT INTO attempts (user_id, paper_id, source)
                VALUES ($1, $2, 'bank')
                RETURNING id
            ''', user_id, q['paper_id'])
            
        # Save Answer record
        import json
        ans_id = await conn.fetchval('''
            INSERT INTO answers (
                attempt_id, question_id, user_id, answer_text, answer_image_url,
                marks_awarded, marks_possible, feedback_text, missed_points,
                misconception_tags, marked_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
        ''',
            attempt_id, q['id'], user_id, req.answer_text, req.answer_image_url,
            marking_result['marks_awarded'], marking_result['marks_possible'],
            marking_result['feedback_text'], json.dumps(marking_result['missed_points']),
            json.dumps(marking_result['misconception_tags']), marking_result['marked_by']
        )
        
        # Update Master Student Profile
        await update_student_profile_after_answer(
            conn=conn,
            user_id=user_id,
            spec_topic_id=str(q['spec_topic_id']) if q['spec_topic_id'] else None,
            spec_code=q['spec_code'],
            command_word=command_word,
            marks_awarded=marking_result['marks_awarded'],
            marks_possible=marking_result['marks_possible'],
            misconception_tags=marking_result['misconception_tags']
        )
        
        return {
            "answer_id": str(ans_id),
            "attempt_id": str(attempt_id),
            "marks_awarded": marking_result['marks_awarded'],
            "marks_possible": marking_result['marks_possible'],
            "marked_by": marking_result['marked_by'],
            "feedback_text": marking_result['feedback_text'],
            "missed_points": marking_result['missed_points'],
            "misconception_tags": marking_result['misconception_tags'],
            "is_full_marks": marking_result['marks_awarded'] == marking_result['marks_possible']
        }
