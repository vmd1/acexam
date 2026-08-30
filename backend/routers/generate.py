import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from database import get_db
from dependencies import rate_limit, get_current_user_id
from profile_engine import calculate_decayed_mastery_scores

router = APIRouter(dependencies=[Depends(rate_limit)])

ROOT_NUMBER_RE = re.compile(r'^0*(\d+)')


def _question_root(question_number: str) -> str:
    # Mirrors PracticeSession.tsx's questionRootKey() so sub-questions like
    # '08.1'/'08.2'/'08.3' are recognised as siblings of the same stem.
    match = ROOT_NUMBER_RE.match(question_number or '')
    return match.group(1) if match else (question_number or '')


async def _expand_to_full_groups(conn, rows, max_groups):
    """Given candidate question rows (possibly only some sub-parts of a
    multi-part question, since candidates are filtered per-row by topic),
    fetch every sibling sub-question sharing the same paper + stem number so
    a group is never served with its earlier parts missing."""
    seen_keys = []
    key_set = set()
    for r in rows:
        key = (r['paper_id'], _question_root(r['question_number']))
        if key not in key_set:
            key_set.add(key)
            seen_keys.append(key)
        if len(seen_keys) >= max_groups:
            break

    if not seen_keys:
        return []

    paper_ids = [k[0] for k in seen_keys]
    roots = [k[1] for k in seen_keys]

    full_rows = await conn.fetch('''
        SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
        FROM questions q
        JOIN papers p ON q.paper_id = p.id
        LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
        WHERE (q.paper_id, substring(q.question_number from '^0*([0-9]+)')) IN (
            SELECT * FROM unnest($1::uuid[], $2::text[])
        )
    ''', paper_ids, roots)

    order_index = {key: i for i, key in enumerate(seen_keys)}
    return sorted(full_rows, key=lambda r: order_index[(r['paper_id'], _question_root(r['question_number']))])


# A question the student has already scored full marks on is excluded from
# candidate selection entirely, so it's never served again once mastered.
# (Sibling sub-questions of a partially-mastered group can still pull a
# mastered row back in via _expand_to_full_groups - that's intentional, so
# the group renders complete - _attach_previous_answers below is what lets
# the frontend show it as already-done rather than re-prompting for it.)
MASTERY_EXCLUSION_SQL = '''NOT EXISTS (
    SELECT 1 FROM answers a
    WHERE a.question_id = q.id AND a.user_id = $1 AND a.marks_awarded = a.marks_possible
)'''


async def _attach_previous_answers(conn, user_id, rows):
    """Attach each question's most recent answer (if any) so the frontend
    can prefill/review what the student already submitted, instead of
    presenting a blank slate for a question they've already attempted."""
    if not rows:
        return []

    question_ids = [r['id'] for r in rows]
    prev_rows = await conn.fetch('''
        SELECT DISTINCT ON (question_id)
            question_id, answer_text, answer_image_url, marks_awarded, marks_possible,
            feedback_text, missed_points, misconception_tags, marked_by, created_at
        FROM answers
        WHERE user_id = $1 AND question_id = ANY($2)
        ORDER BY question_id, created_at DESC
    ''', user_id, question_ids)
    prev_by_qid = {r['question_id']: dict(r) for r in prev_rows}

    result = []
    for r in rows:
        d = dict(r)
        prev = prev_by_qid.get(r['id'])
        if prev:
            prev = dict(prev)
            prev.pop('question_id', None)
            prev['created_at'] = prev['created_at'].isoformat() if prev.get('created_at') else None
        d['previous_answer'] = prev
        result.append(d)
    return result

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
        
        # Fetch matching questions or fallback to general bank. Questions the
        # student has already scored full marks on are excluded so a
        # mastered question is never served again.
        query = f'''
            SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
            FROM questions q
            JOIN papers p ON q.paper_id = p.id
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            WHERE {MASTERY_EXCLUSION_SQL}
        '''

        conditions = []
        params = [user_id]
        if target_topic_ids:
            params.append(target_topic_ids)
            conditions.append(f"q.spec_topic_id = ANY(${len(params)})")

        if conditions:
            query += " AND (" + " OR ".join(conditions) + ")"

        query += " ORDER BY RANDOM() LIMIT 10"

        questions = await conn.fetch(query, *params)

        # If not enough questions matched, backfill from general pool
        if len(questions) < 5:
            fallback = await conn.fetch(f'''
                SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
                FROM questions q
                JOIN papers p ON q.paper_id = p.id
                LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
                WHERE {MASTERY_EXCLUSION_SQL}
                ORDER BY RANDOM()
                LIMIT 10
            ''', user_id)
            questions = list({q['id']: q for q in (list(questions) + list(fallback))}.values())[:10]

        # Candidates above are matched per sub-question, so a multi-part
        # question can come back with only its later parts (the ones that
        # happen to match the target topic/fallback). Expand each candidate
        # to its full sibling group so students never see e.g. 08.3-08.6
        # without 08.1-08.2.
        questions = await _expand_to_full_groups(conn, questions, max_groups=6)
        questions = await _attach_previous_answers(conn, user_id, questions)

        return {
            "queue": questions,
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
        conditions = [MASTERY_EXCLUSION_SQL, "p.subject = $2"]
        params = [user_id, req.subject]

        if req.exam_board:
            params.append(req.exam_board)
            conditions.append(f"p.exam_board = ${len(params)}")

        if req.spec_topic_ids:
            params.append(req.spec_topic_ids)
            conditions.append(f"q.spec_topic_id = ANY(${len(params)})")

        query = f'''
            SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
            FROM questions q
            JOIN papers p ON q.paper_id = p.id
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            WHERE {' AND '.join(conditions)}
            ORDER BY RANDOM()
            LIMIT 10
        '''

        questions = await conn.fetch(query, *params)
        questions = await _expand_to_full_groups(conn, questions, max_groups=6)
        questions = await _attach_previous_answers(conn, user_id, questions)

        attempt_id = await conn.fetchval('''
            INSERT INTO attempts (user_id, paper_id, source)
            VALUES ($1, NULL, 'custom_generated')
            RETURNING id
        ''', user_id)
        
        return {
            "attempt_id": str(attempt_id),
            "title": f"Custom {req.subject} Mock Paper",
            "questions": questions
        }
