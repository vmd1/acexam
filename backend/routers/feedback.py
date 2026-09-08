import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from database import get_db
from dependencies import rate_limit, get_current_user_id
from marking_engine import mark_question
from profile_engine import update_student_profile_after_answer

router = APIRouter(dependencies=[Depends(rate_limit)])

# Standard AQA/Edexcel/OCR GCSE & A-level command words, longest-first so a
# multi-word phrase (e.g. "Show that") matches before its single-word prefix.
_COMMAND_WORDS = sorted([
    "Show that", "Suggest why", "Explain why",
    "Explain", "Describe", "Calculate", "Compare", "Evaluate", "Discuss",
    "Justify", "Suggest", "Outline", "Analyse", "Analyze", "Assess", "Define",
    "Determine", "Estimate", "Complete", "Draw", "Plot", "Label", "Sketch",
    "Predict", "Deduce", "Balance", "Identify", "State", "Give", "Name",
    "List", "Write", "Give reasons", "Give a reason",
], key=len, reverse=True)
_COMMAND_WORD_RE = re.compile(
    r'\b(' + '|'.join(re.escape(w) for w in _COMMAND_WORDS) + r')\b', re.IGNORECASE
)


def _detect_command_word(question_text: Optional[str]) -> str:
    """Find the first real exam command word anywhere in the question stem,
    rather than assuming it's the first word (which is often the question
    number, a scene-setting sentence, or a figure reference)."""
    if question_text:
        match = _COMMAND_WORD_RE.search(question_text)
        if match:
            return match.group(1).capitalize()
    return "Explain"

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
            SELECT q.*, st.spec_code, p.exam_board, p.level, p.subject, p.tier
            FROM questions q
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            JOIN papers p ON q.paper_id = p.id
            WHERE q.id = $1
        ''', req.question_id)

        if not q:
            raise HTTPException(status_code=404, detail="Question not found")

        # First name only (not the full display_name) - AI feedback addresses
        # the student directly (e.g. "Bob, you did well here..."), and a full
        # name there reads oddly formal for that.
        display_name = await conn.fetchval('SELECT display_name FROM users WHERE id = $1', user_id)
        student_first_name = (display_name or '').strip().split(' ')[0] or None

        # Detect command word if not passed
        command_word = req.command_word or _detect_command_word(q['question_text'])

        # §6.3/§6.4 rollout gate: only call the self-hosted model for this
        # spec code once an admin has flipped it to 'live'. No row at all
        # (no adapter ever trained for this qualification) behaves the same
        # as 'none' - mark_question falls back to needs_review either way.
        model_row = await conn.fetchrow('''
            SELECT status FROM spec_code_marking_models
            WHERE exam_board = $1 AND level = $2 AND subject = $3 AND tier = $4
        ''', q['exam_board'], q['level'], q['subject'], q['tier'] or '')
        ai_marking_status = model_row['status'] if model_row else 'none'

        # Misconception tags are constrained to this spec code's *approved*
        # taxonomy (mirrors ingestion.py's known_misconceptions for the
        # independent grader) - an unapproved/free-text tag isn't trustworthy
        # enough to attach to a real student's profile yet.
        known_misconceptions = []
        if q['spec_code']:
            taxonomy_rows = await conn.fetch(
                '''SELECT tag_id, label FROM misconception_taxonomy
                   WHERE spec_code = $1 AND approved_at IS NOT NULL''',
                q['spec_code']
            )
            known_misconceptions = [{"tag_id": r["tag_id"], "label": r["label"]} for r in taxonomy_rows]

        # Evaluate student answer
        marking_result = await mark_question(
            question_text=q['question_text'],
            mark_value=q['mark_value'],
            marking_type=q['marking_type'],
            marking_dsl=q['marking_dsl'],
            mark_scheme_text=q['mark_scheme_text'],
            student_answer=req.answer_text,
            command_word=command_word,
            spec_code=q['spec_code'],
            ai_marking_status=ai_marking_status,
            exam_board=q['exam_board'],
            level=q['level'],
            subject=q['subject'],
            tier=q['tier'],
            answer_options=q['answer_options'],
            student_first_name=student_first_name,
            known_misconceptions=known_misconceptions,
            table_data=q['table_data']
        )

        # A confidently-identified misconception with no approved tag close
        # enough feeds the same admin-approval queue the ingestion-time
        # scanner uses (misconception_taxonomy, approved_at NULL) - it never
        # gets attached to this student's profile until an admin approves it,
        # same as any other pending taxonomy entry.
        new_tag_suggestion = marking_result.get('new_tag_suggestion')
        if new_tag_suggestion and q['spec_code']:
            await conn.execute('''
                INSERT INTO misconception_taxonomy (spec_code, tag_id, label, description, approved_at)
                VALUES ($1, $2, $3, $4, NULL)
                ON CONFLICT (spec_code, tag_id) DO NOTHING
            ''', q['spec_code'], new_tag_suggestion['tag_id'], new_tag_suggestion['label'], new_tag_suggestion['description'])

        # Ensure attempt exists, and belongs to the caller if one was passed
        # in (attempt_id now gets threaded through real practice sessions
        # and saved papers, not just generated server-side, so it's no
        # longer safe to trust a client-supplied id without checking).
        attempt_id = req.attempt_id
        if attempt_id:
            owns_attempt = await conn.fetchval(
                'SELECT 1 FROM attempts WHERE id = $1 AND user_id = $2', attempt_id, user_id
            )
            if not owns_attempt:
                raise HTTPException(status_code=404, detail="Attempt not found")
        else:
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
                marks_awarded, marks_possible, feedback_text, www, missed_points,
                misconception_tags, marked_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING id
        ''',
            attempt_id, q['id'], user_id, req.answer_text, req.answer_image_url,
            marking_result['marks_awarded'], marking_result['marks_possible'],
            marking_result['feedback_text'], json.dumps(marking_result['www']),
            json.dumps(marking_result['missed_points']),
            json.dumps(marking_result['misconception_tags']), marking_result['marked_by']
        )
        
        # Update Master Student Profile - skipped for a 'pending_model'
        # result (§6.4: AI marking not yet available for this spec code),
        # since there's no real mark yet to fold into mastery/EWMA.
        if marking_result['marked_by'] != 'pending_model':
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
            "www": marking_result['www'],
            "missed_points": marking_result['missed_points'],
            "misconception_tags": marking_result['misconception_tags'],
            "is_full_marks": marking_result['marks_awarded'] == marking_result['marks_possible']
        }
