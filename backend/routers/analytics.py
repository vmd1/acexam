from fastapi import APIRouter, Depends, HTTPException
from database import get_db
from dependencies import rate_limit, get_current_user_id
from profile_engine import calculate_decayed_mastery_scores

router = APIRouter(dependencies=[Depends(rate_limit)])

@router.get("/profile")
async def get_analytics_profile(
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
        
    async with db.acquire() as conn:
        await calculate_decayed_mastery_scores(conn, user_id)
        
        # 1. Topic Mastery Tree
        topics = await conn.fetch('''
            SELECT st.id, st.spec_code, st.title, st.subject, st.exam_board, st.parent_id,
                   COALESCE(stm.mastery_score, 0.0) as mastery_score,
                   COALESCE(stm.decay_score, 0.0) as decay_score,
                   COALESCE(stm.attempts_count, 0) as attempts_count,
                   stm.last_practiced_at
            FROM spec_topics st
            LEFT JOIN student_topic_mastery stm ON st.id = stm.spec_topic_id AND stm.user_id = $1
            ORDER BY st.spec_code ASC
        ''', user_id)
        
        # 2. Command Word Competency Matrix
        command_words = await conn.fetch('''
            SELECT command_word, marks_awarded, marks_possible,
                   CASE WHEN marks_possible > 0 THEN ROUND((marks_awarded::numeric / marks_possible) * 100, 1) ELSE 0 END as accuracy_pct
            FROM student_command_word_mastery
            WHERE user_id = $1
            ORDER BY marks_possible DESC
        ''', user_id)
        
        # 3. Misconceptions
        misconceptions = await conn.fetch('''
            SELECT sm.spec_code, sm.tag_id, sm.occurrences, sm.consecutive_correct, sm.status, sm.last_seen_at,
                   mt.label, mt.description
            FROM student_misconceptions sm
            LEFT JOIN misconception_taxonomy mt ON sm.spec_code = mt.spec_code AND sm.tag_id = mt.tag_id
            WHERE sm.user_id = $1
            ORDER BY sm.status ASC, sm.occurrences DESC
        ''', user_id)
        
        # 4. Global statistics
        stats = await conn.fetchrow('''
            SELECT COUNT(id) as total_answers,
                   COALESCE(SUM(marks_awarded), 0) as total_marks_earned,
                   COALESCE(SUM(marks_possible), 0) as total_marks_possible
            FROM answers
            WHERE user_id = $1
        ''', user_id)
        
        total_answers = stats['total_answers'] if stats else 0
        total_earned = stats['total_marks_earned'] if stats else 0
        total_possible = stats['total_marks_possible'] if stats else 0
        overall_pct = (total_earned / total_possible * 100) if total_possible > 0 else 0
        
        # Predicted GCSE Grade (9-1 boundary projection)
        predicted_grade = "N/A"
        if total_possible >= 10:
            if overall_pct >= 85: predicted_grade = "Grade 9"
            elif overall_pct >= 75: predicted_grade = "Grade 8"
            elif overall_pct >= 65: predicted_grade = "Grade 7"
            elif overall_pct >= 55: predicted_grade = "Grade 6"
            elif overall_pct >= 45: predicted_grade = "Grade 5"
            elif overall_pct >= 35: predicted_grade = "Grade 4"
            else: predicted_grade = "Grade 3"

        return {
            "predicted_grade": predicted_grade,
            "overall_accuracy_pct": round(overall_pct, 1),
            "total_answers": total_answers,
            "total_marks_earned": total_earned,
            "total_marks_possible": total_possible,
            "topic_mastery": [dict(t) for t in topics],
            "command_word_competency": [dict(cw) for cw in command_words],
            "misconceptions": [dict(m) for m in misconceptions]
        }
