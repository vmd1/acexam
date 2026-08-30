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


async def _expand_to_full_groups(conn, rows, max_groups=None):
    """Given candidate question rows (possibly only some sub-parts of a
    multi-part question, since candidates are filtered per-row by topic),
    fetch every sibling sub-question sharing the same paper + stem number so
    a group is never served with its earlier parts missing. max_groups=None
    expands every distinct group present in rows (used when the caller
    trims to a mark target afterwards instead of a fixed group count)."""
    seen_keys = []
    key_set = set()
    for r in rows:
        key = (r['paper_id'], _question_root(r['question_number']))
        if key not in key_set:
            key_set.add(key)
            seen_keys.append(key)
        if max_groups is not None and len(seen_keys) >= max_groups:
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

# A custom paper is meant to feel like a fresh timed mock exam rather than a
# revision drill, so - unlike the adaptive queue's MASTERY_EXCLUSION_SQL,
# which only excludes a question once fully mastered - any question with
# ANY prior attempt at all (even an unfinished/partial one) is excluded
# from candidate selection.
NOT_ATTEMPTED_SQL = '''NOT EXISTS (
    SELECT 1 FROM answers a
    WHERE a.question_id = q.id AND a.user_id = $1
)'''


async def _drop_groups_with_attempted_siblings(conn, user_id, rows):
    """_expand_to_full_groups can pull an already-attempted sibling back
    into a group (e.g. 08.1 was answered, 08.2 wasn't, but both belong to
    the same stem) - intentional for the adaptive queue so a group renders
    complete, but wrong for a custom paper, which should never resurface a
    stem the student has partly done. Drop the whole (paper_id, root) group
    in that case instead of serving a mixed fresh/already-done group."""
    if not rows:
        return []
    question_ids = [r['id'] for r in rows]
    attempted_rows = await conn.fetch(
        'SELECT DISTINCT question_id FROM answers WHERE user_id = $1 AND question_id = ANY($2)',
        user_id, question_ids
    )
    attempted_ids = {r['question_id'] for r in attempted_rows}
    tainted_groups = {
        (r['paper_id'], _question_root(r['question_number']))
        for r in rows if r['id'] in attempted_ids
    }
    return [r for r in rows if (r['paper_id'], _question_root(r['question_number'])) not in tainted_groups]


def _trim_groups_to_target_marks(rows, target_marks):
    """Keeps whole (paper_id, root) groups, in the order they already
    appear, accumulating mark_value until the running total reaches
    target_marks - the group that crosses the threshold is kept in full
    (a paper should never cut a question in half), so the final total can
    slightly overshoot the target but never stops mid-question. Stops
    early if the candidate pool runs out first (a small bank just gives
    everything it has, same best-effort philosophy as the adaptive queue's
    fallback). target_marks=None returns rows unchanged."""
    if target_marks is None or not rows:
        return rows

    order = []
    seen = set()
    marks_by_key = {}
    rows_by_key = {}
    for r in rows:
        key = (r['paper_id'], _question_root(r['question_number']))
        if key not in seen:
            seen.add(key)
            order.append(key)
            marks_by_key[key] = 0
            rows_by_key[key] = []
        marks_by_key[key] += r['mark_value']
        rows_by_key[key].append(r)

    kept_keys = []
    running = 0
    for key in order:
        if running >= target_marks:
            break
        kept_keys.append(key)
        running += marks_by_key[key]

    result = []
    for key in kept_keys:
        result.extend(rows_by_key[key])
    return result


def _renumber_for_custom_paper(rows):
    """A custom paper is stitched together from questions scattered across
    the bank, so their original question_numbers (e.g. "07.1", "03.2")
    read as out-of-order noise once reassembled - a real paper numbers
    sequentially from 1. Renumbers each group to its position in THIS
    paper while preserving each sub-question's own suffix (the ".2" /
    "(b)(ii)" part after the leading digits), so a multi-part stem still
    reads as one connected question, just under its new position."""
    if not rows:
        return rows
    root_to_new = {}
    for r in rows:
        key = (r['paper_id'], _question_root(r['question_number']))
        if key not in root_to_new:
            root_to_new[key] = str(len(root_to_new) + 1)

    renumbered = []
    for r in rows:
        d = dict(r)
        key = (r['paper_id'], _question_root(r['question_number']))
        suffix = ROOT_NUMBER_RE.sub('', d['question_number'] or '', count=1)
        d['question_number'] = f"{root_to_new[key]}{suffix}"
        renumbered.append(d)
    return renumbered


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
    level: Optional[str] = "GCSE"
    spec_topic_ids: Optional[List[str]] = []

# Used when this (exam_board, level, subject) has no admin-configured
# custom_paper_target_marks yet (Manage Subjects, §qualifications).
DEFAULT_CUSTOM_PAPER_TARGET_MARKS = 20
# ~1 minute per mark is the standard GCSE exam-technique rule of thumb,
# used only when there's no configured custom_paper_time_limit_minutes.
DEFAULT_SECONDS_PER_MARK = 60
MIN_CUSTOM_PAPER_TIME_LIMIT_SECONDS = 600

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
        # mastered question is never served again. Also excludes questions
        # under a Higher-tier-only topic when the student is Foundation tier
        # for that subject (or their tier for it is unknown) -- specs merge
        # both tiers into one document and flag some content as HT-only, see
        # spec_topics.tier_only.
        TIER_JOIN_SQL = '''LEFT JOIN user_subjects us
            ON us.user_id = $1 AND us.exam_board = p.exam_board
            AND us.subject = p.subject AND us.level = p.level'''
        TIER_FILTER_SQL = '(st.tier_only IS NULL OR st.tier_only = us.tier)'

        query = f'''
            SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
            FROM questions q
            JOIN papers p ON q.paper_id = p.id
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            {TIER_JOIN_SQL}
            WHERE p.status = 'published' AND {MASTERY_EXCLUSION_SQL} AND {TIER_FILTER_SQL}
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
                {TIER_JOIN_SQL}
                WHERE p.status = 'published' AND {MASTERY_EXCLUSION_SQL} AND {TIER_FILTER_SQL}
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
        # The student's tier for this subject (if any) - a tiered GCSE spec
        # merges Higher and Foundation content into one document and flags
        # some topics as Higher-only (spec_topics.tier_only), so Foundation
        # students must never be served those. Unknown tier is treated the
        # same as Foundation here (excludes tier-restricted content) since
        # that's the safer default.
        student_tier = await conn.fetchval(
            'SELECT tier FROM user_subjects WHERE user_id = $1 AND exam_board = $2 AND subject = $3 AND level = $4 LIMIT 1',
            user_id, req.exam_board, req.subject, req.level
        )

        # Admin-configured mark total/time limit for this qualification
        # (Manage Subjects, §qualifications) - falls back to the previous
        # heuristics when nothing's been configured for it yet.
        qualification = await conn.fetchrow(
            '''SELECT custom_paper_target_marks, custom_paper_time_limit_minutes
               FROM qualifications WHERE exam_board = $1 AND level = $2 AND subject = $3''',
            req.exam_board, req.level, req.subject
        )
        target_marks = (qualification['custom_paper_target_marks'] if qualification else None) or DEFAULT_CUSTOM_PAPER_TARGET_MARKS
        configured_time_limit_minutes = qualification['custom_paper_time_limit_minutes'] if qualification else None

        conditions = [NOT_ATTEMPTED_SQL, "p.status = 'published'", "p.subject = $2", "p.level = $3"]
        params = [user_id, req.subject, req.level]

        if req.exam_board:
            params.append(req.exam_board)
            conditions.append(f"p.exam_board = ${len(params)}")

        if req.spec_topic_ids:
            params.append(req.spec_topic_ids)
            conditions.append(f"q.spec_topic_id = ANY(${len(params)})")

        params.append(student_tier)
        conditions.append(f"(st.tier_only IS NULL OR st.tier_only = ${len(params)})")

        # A generous candidate pool, not a fixed question count - how much
        # of it actually gets used is decided by _trim_groups_to_target_marks
        # below, against the qualification's real mark total.
        query = f'''
            SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
            FROM questions q
            JOIN papers p ON q.paper_id = p.id
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            WHERE {' AND '.join(conditions)}
            ORDER BY RANDOM()
            LIMIT 200
        '''

        questions = await conn.fetch(query, *params)
        # _expand_to_full_groups mirrors the adaptive queue's grouping rules
        # exactly (same helper) so a stem never renders with early sub-parts
        # missing. No group-count cap here (unlike the adaptive queue) -
        # _trim_groups_to_target_marks decides how much to keep, by marks.
        questions = await _expand_to_full_groups(conn, questions)
        questions = await _drop_groups_with_attempted_siblings(conn, user_id, questions)
        questions = _trim_groups_to_target_marks(questions, target_marks)
        questions = await _attach_previous_answers(conn, user_id, questions)
        # Renumber last - grouping/trimming above all key off the original
        # question_number, so it must stay untouched until they're done.
        questions = _renumber_for_custom_paper(questions)

        attempt_id = await conn.fetchval('''
            INSERT INTO attempts (user_id, paper_id, source)
            VALUES ($1, NULL, 'custom_generated')
            RETURNING id
        ''', user_id)

        total_marks = sum(q['mark_value'] for q in questions)
        if configured_time_limit_minutes:
            time_limit_seconds = configured_time_limit_minutes * 60
        else:
            time_limit_seconds = max(total_marks * DEFAULT_SECONDS_PER_MARK, MIN_CUSTOM_PAPER_TIME_LIMIT_SECONDS)

        return {
            "attempt_id": str(attempt_id),
            "title": f"Custom {req.subject} Mock Paper",
            "questions": questions,
            "total_marks": total_marks,
            "target_marks": target_marks,
            "time_limit_seconds": time_limit_seconds
        }
