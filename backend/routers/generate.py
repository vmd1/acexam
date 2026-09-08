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


_NATURAL_SORT_CHUNK_RE = re.compile(r'(\d+)')


def _natural_sort_key(question_number: str):
    # Mirrors the frontend's localeCompare(..., {numeric: true}) so a
    # multi-part question's sub-parts ('1.2' before '1.10', not after) sort
    # the same way server-side as they already render client-side - the
    # canonical stored order (attempt_questions.position, the renumbering
    # below) should match what students actually see, not rely on the
    # frontend's own re-sort to paper over an unordered fetch.
    parts = _NATURAL_SORT_CHUNK_RE.split(question_number or '')
    return [int(p) if p.isdigit() else p for p in parts]


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
    return sorted(full_rows, key=lambda r: (
        order_index[(r['paper_id'], _question_root(r['question_number']))],
        _natural_sort_key(r['question_number'])
    ))


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


def _normalize_qnum_for_match(s):
    # Loosely normalizes a question-number string for equality comparison,
    # mirroring ai_pipeline.py's _normalize_question_number: strips
    # whitespace, lowercases, and drops leading zeros within each numeric
    # run, so "03.3" and "3.3" (or "3 . 3") are recognised as the same
    # question despite exam boards not zero-padding consistently.
    s = (s or '').strip().lower()
    s = re.sub(r'\s+', '', s)
    s = re.sub(r'(?<![0-9])0+(?=[0-9])', '', s)
    return s


# Matches an in-text cross-reference to another question by its ORIGINAL
# (pre-renumbering) number, e.g. "Question 03.3", "question 3.2",
# "Question 01". Deliberately requires the literal word "Question" (not a
# bare number, and not the "Q3.2" abbreviation) to keep this narrow - a
# bare number in running text is far too likely to be genuine exam content
# (a quantity, a figure count, etc.) rather than a cross-reference, and
# only rewriting the unambiguous "Question <number>" phrasing keeps the
# false-positive risk low. Only covers the dot-decimal numbering style
# (AQA's "03.3") confirmed by the reported leak - a bracketed-suffix style
# like "3(b)(ii)" (see questions.question_number's own format, used for
# root-grouping) isn't known to appear as an in-text cross-reference in
# practice, and deliberately isn't matched here to avoid a boundary that's
# ambiguous between "end of the reference" and "start of surrounding
# punctuation".
QUESTION_REF_RE = re.compile(
    r'\bQuestion\s+(\d+(?:\.\d+)*)\b',
    re.IGNORECASE
)


def _remap_question_refs(text, paper_id, ref_map):
    """Rewrites in-text "Question <old number>" cross-references (e.g. "the
    hormone you named in Question 03.3") to the new display number that
    question got renumbered to in this custom paper (e.g. "Question 8.3").
    Only rewrites a reference if it resolves to a real question from the
    SAME source paper (ref_map is keyed per paper_id, since two different
    source papers can each have their own "question 1" - a bare number
    can't plausibly cross-reference a question from a different paper) and
    only if that referenced question was actually included in THIS custom
    paper's mapping. A reference to a question that exists in the source
    paper but got filtered out of this custom paper (e.g. by the topic
    filter) has no entry to resolve to - that's left untouched rather than
    guess-rewritten, since a dangling cross-reference to a question the
    student never sees is a separate, pre-existing problem this fix doesn't
    attempt to solve, and silently mismatching it to some other question
    would be worse than leaving the stale number in place."""
    if not text or paper_id not in ref_map:
        return text
    paper_refs = ref_map[paper_id]

    def _replace(m):
        old_norm = _normalize_qnum_for_match(m.group(1))
        new_number = paper_refs.get(old_norm)
        if new_number is None:
            return m.group(0)
        return f"Question {new_number}"

    return QUESTION_REF_RE.sub(_replace, text)


def _renumber_for_custom_paper(rows):
    """A custom paper is stitched together from questions scattered across
    the bank, so their original question_numbers (e.g. "07.1", "03.2")
    read as out-of-order noise once reassembled - a real paper numbers
    sequentially from 1. Renumbers each group to its position in THIS
    paper while preserving each sub-question's own suffix (the ".2" /
    "(b)(ii)" part after the leading digits), so a multi-part stem still
    reads as one connected question, just under its new position.

    Also rewrites in-text cross-references within question_text that refer
    to another question by its old number (e.g. "...the hormone you named
    in Question 03.3...") so they point at the new number the referenced
    question ended up with here, instead of leaking a stale original-paper
    number that means nothing to the student. See _remap_question_refs for
    the matching/fallback rules."""
    if not rows:
        return rows
    root_to_new = {}
    # Per-paper old(normalized full number) -> new full number, built
    # alongside the root renumbering below so the two can never disagree -
    # this reuses the exact same key/assignment the metadata renumbering
    # already computes rather than re-deriving a mapping separately.
    ref_map = {}
    for r in rows:
        key = (r['paper_id'], _question_root(r['question_number']))
        if key not in root_to_new:
            root_to_new[key] = str(len(root_to_new) + 1)

    renumbered = []
    for r in rows:
        d = dict(r)
        key = (r['paper_id'], _question_root(r['question_number']))
        suffix = ROOT_NUMBER_RE.sub('', d['question_number'] or '', count=1)
        new_number = f"{root_to_new[key]}{suffix}"
        d['question_number'] = new_number
        ref_map.setdefault(r['paper_id'], {})[
            _normalize_qnum_for_match(r['question_number'])
        ] = new_number
        renumbered.append(d)

    for d in renumbered:
        d['question_text'] = _remap_question_refs(
            d.get('question_text'), d['paper_id'], ref_map
        )

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
            feedback_text, www, missed_points, misconception_tags, marked_by, created_at
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

async def _attach_answers_for_attempt(conn, attempt_id, rows):
    """Like _attach_previous_answers, but scoped to one specific attempt_id
    instead of "most recent answer anywhere" - used when reopening a saved
    paper/session so it shows exactly what was submitted within THAT
    attempt (and a later re-attempt inside the same attempt_id naturally
    supersedes it, since we still take the most recent by created_at)."""
    if not rows:
        return []

    question_ids = [r['id'] for r in rows]
    prev_rows = await conn.fetch('''
        SELECT DISTINCT ON (question_id)
            question_id, answer_text, answer_image_url, marks_awarded, marks_possible,
            feedback_text, www, missed_points, misconception_tags, marked_by, created_at
        FROM answers
        WHERE attempt_id = $1 AND question_id = ANY($2)
        ORDER BY question_id, created_at DESC
    ''', attempt_id, question_ids)
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


def _parse_id_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [v for v in (part.strip() for part in raw.split(',')) if v]


async def _record_attempt_questions(conn, attempt_id, question_ids, start_position=0):
    if not question_ids:
        return
    rows = [(attempt_id, qid, start_position + i) for i, qid in enumerate(question_ids)]
    await conn.executemany(
        'INSERT INTO attempt_questions (attempt_id, question_id, position) VALUES ($1, $2, $3) '
        'ON CONFLICT (attempt_id, question_id) DO NOTHING',
        rows
    )


class CustomPaperRequest(BaseModel):
    subject: Optional[str] = "Biology"
    exam_board: Optional[str] = "AQA"
    level: Optional[str] = "GCSE"
    spec_topic_ids: Optional[List[str]] = []

# Used when this (exam_board, level, subject) has no admin-configured
# custom_paper_target_marks yet (Manage Subjects, §qualifications) - 100 is
# the standard total for a GCSE paper (matches real exam length, and the
# admin-configured values already in use for other qualifications).
DEFAULT_CUSTOM_PAPER_TARGET_MARKS = 100
# ~1 minute per mark is the standard GCSE exam-technique rule of thumb,
# used only when there's no configured custom_paper_time_limit_minutes.
DEFAULT_SECONDS_PER_MARK = 60
MIN_CUSTOM_PAPER_TIME_LIMIT_SECONDS = 600

async def _select_adaptive_candidates(
    conn, user_id, subject, exam_board, level, topic_mode, topic_ids, exclude_ids, max_groups=6
):
    """Shared candidate-selection logic for the adaptive/exam-questions
    queue, used both by the initial GET and the endless "more" POST. Returns
    (questions, focus_reason). topic_mode is 'weak' (default - auto-targets
    decaying topics/misconceptions), 'random' (ignores weakness targeting,
    just samples the filtered pool), or 'select' (explicit topic_ids)."""
    await calculate_decayed_mastery_scores(conn, user_id)

    active_misconceptions = await conn.fetch('''
        SELECT spec_code, tag_id, occurrences
        FROM student_misconceptions
        WHERE user_id = $1 AND status = 'active'
        ORDER BY occurrences DESC
        LIMIT 3
    ''', user_id)

    weak_topics_conditions = ["stm.user_id = $1"]
    weak_topics_params = [user_id]
    if subject:
        weak_topics_params.append(exam_board)
        weak_topics_conditions.append(f"st.exam_board = ${len(weak_topics_params)}")
        weak_topics_params.append(subject)
        weak_topics_conditions.append(f"st.subject = ${len(weak_topics_params)}")
        weak_topics_params.append(level)
        weak_topics_conditions.append(f"st.level = ${len(weak_topics_params)}")

    weak_topics = await conn.fetch(f'''
        SELECT stm.spec_topic_id, stm.decay_score, stm.mastery_score
        FROM student_topic_mastery stm
        JOIN spec_topics st ON stm.spec_topic_id = st.id
        WHERE {' AND '.join(weak_topics_conditions)}
        ORDER BY stm.decay_score ASC
        LIMIT 3
    ''', *weak_topics_params)

    weak_commands = await conn.fetch('''
        SELECT command_word,
               CASE WHEN marks_possible > 0 THEN (marks_awarded::float / marks_possible) ELSE 0.0 END as acc
        FROM student_command_word_mastery
        WHERE user_id = $1 AND marks_possible >= 3
        ORDER BY acc ASC
        LIMIT 2
    ''', user_id)

    TIER_JOIN_SQL = '''LEFT JOIN user_subjects us
        ON us.user_id = $1 AND us.exam_board = p.exam_board
        AND us.subject = p.subject AND us.level = p.level'''
    TIER_FILTER_SQL = '(st.tier_only IS NULL OR st.tier_only = us.tier)'

    base_conditions = ["p.status = 'published'", MASTERY_EXCLUSION_SQL, TIER_FILTER_SQL]
    params = [user_id]

    if subject:
        params.append(subject)
        base_conditions.append(f"p.subject = ${len(params)}")
        params.append(exam_board)
        base_conditions.append(f"p.exam_board = ${len(params)}")
        params.append(level)
        base_conditions.append(f"p.level = ${len(params)}")

    if exclude_ids:
        params.append(exclude_ids)
        base_conditions.append(f"NOT (q.id = ANY(${len(params)}))")

    # A question can be classified under more than one spec topic (see
    # question_topics - schema_phase25) - match against ANY of a question's
    # topics, not just its primary q.spec_topic_id, so a multi-topic
    # question surfaces whenever a student targets one it actually covers.
    if topic_mode == 'select' and topic_ids:
        params.append(topic_ids)
        base_conditions.append(
            f"EXISTS (SELECT 1 FROM question_topics qt WHERE qt.question_id = q.id AND qt.spec_topic_id = ANY(${len(params)}))"
        )
    elif topic_mode != 'random':
        target_topic_ids = [str(r['spec_topic_id']) for r in weak_topics]
        if target_topic_ids:
            params.append(target_topic_ids)
            base_conditions.append(
                f"EXISTS (SELECT 1 FROM question_topics qt WHERE qt.question_id = q.id AND qt.spec_topic_id = ANY(${len(params)}))"
            )

    query = f'''
        SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
        FROM questions q
        JOIN papers p ON q.paper_id = p.id
        LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
        {TIER_JOIN_SQL}
        WHERE {' AND '.join(base_conditions)}
        ORDER BY RANDOM() LIMIT 10
    '''

    questions = await conn.fetch(query, *params)

    # If not enough questions matched, backfill from the general (still
    # subject/exclude-filtered, but un-topic-targeted) pool.
    if len(questions) < 5:
        fallback_conditions = ["p.status = 'published'", MASTERY_EXCLUSION_SQL, TIER_FILTER_SQL]
        fallback_params = [user_id]
        if subject:
            fallback_params.append(subject)
            fallback_conditions.append(f"p.subject = ${len(fallback_params)}")
            fallback_params.append(exam_board)
            fallback_conditions.append(f"p.exam_board = ${len(fallback_params)}")
            fallback_params.append(level)
            fallback_conditions.append(f"p.level = ${len(fallback_params)}")
        if exclude_ids:
            fallback_params.append(exclude_ids)
            fallback_conditions.append(f"NOT (q.id = ANY(${len(fallback_params)}))")

        fallback = await conn.fetch(f'''
            SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
            FROM questions q
            JOIN papers p ON q.paper_id = p.id
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            {TIER_JOIN_SQL}
            WHERE {' AND '.join(fallback_conditions)}
            ORDER BY RANDOM()
            LIMIT 10
        ''', *fallback_params)
        questions = list({q['id']: q for q in (list(questions) + list(fallback))}.values())[:10]

    # Candidates above are matched per sub-question, so a multi-part
    # question can come back with only its later parts (the ones that
    # happen to match the target topic/fallback). Expand each candidate
    # to its full sibling group so students never see e.g. 08.3-08.6
    # without 08.1-08.2.
    questions = await _expand_to_full_groups(conn, questions, max_groups=max_groups)
    questions = await _attach_previous_answers(conn, user_id, questions)

    focus_reason = {
        "active_misconceptions_count": len(active_misconceptions),
        "weak_topics_count": len(weak_topics),
        "weakest_command_words": [r['command_word'] for r in weak_commands]
    }
    return questions, focus_reason


@router.get("/adaptive-queue")
async def get_adaptive_queue(
    subject: Optional[str] = None,
    exam_board: Optional[str] = None,
    level: Optional[str] = None,
    topic_mode: str = 'weak',
    topic_ids: Optional[str] = None,
    exclude_ids: Optional[str] = None,
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    async with db.acquire() as conn:
        questions, focus_reason = await _select_adaptive_candidates(
            conn, user_id, subject, exam_board, level, topic_mode,
            _parse_id_list(topic_ids), _parse_id_list(exclude_ids)
        )

        # A subject-scoped call is the new per-subject "exam questions" flow
        # (endless feed); the legacy no-args call from the generic adaptive
        # queue keeps its original 'adaptive' labelling.
        mode = 'exam_questions' if subject else 'adaptive'
        attempt_id = await conn.fetchval('''
            INSERT INTO attempts (user_id, paper_id, source, mode, title, subject, exam_board, level)
            VALUES ($1, NULL, 'bank', $2, $3, $4, $5, $6)
            RETURNING id
        ''', user_id, mode, (f"{subject} Exam Questions" if subject else "Adaptive Session"),
            subject, exam_board, level)
        await _record_attempt_questions(conn, attempt_id, [q['id'] for q in questions])

        return {
            "attempt_id": str(attempt_id),
            "queue": questions,
            "focus_reason": focus_reason
        }


@router.post("/adaptive-queue/{attempt_id}/more")
async def get_more_adaptive_questions(
    attempt_id: str,
    topic_mode: str = 'weak',
    topic_ids: Optional[str] = None,
    exclude_ids: Optional[str] = None,
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    async with db.acquire() as conn:
        attempt = await conn.fetchrow(
            'SELECT * FROM attempts WHERE id = $1 AND user_id = $2', attempt_id, user_id
        )
        if not attempt:
            raise HTTPException(status_code=404, detail="Session not found")

        questions, _ = await _select_adaptive_candidates(
            conn, user_id, attempt['subject'], attempt['exam_board'], attempt['level'],
            topic_mode, _parse_id_list(topic_ids), _parse_id_list(exclude_ids)
        )

        next_position = await conn.fetchval(
            'SELECT COALESCE(MAX(position), -1) + 1 FROM attempt_questions WHERE attempt_id = $1',
            attempt_id
        )
        await _record_attempt_questions(conn, attempt_id, [q['id'] for q in questions], next_position)

        return {"attempt_id": attempt_id, "queue": questions}


@router.get("/attempts")
async def list_attempts(
    mode: Optional[str] = None,
    subject: Optional[str] = None,
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    async with db.acquire() as conn:
        conditions = ["a.user_id = $1", "a.mode IS NOT NULL"]
        params = [user_id]
        if mode:
            params.append(mode)
            conditions.append(f"a.mode = ${len(params)}")
        if subject:
            params.append(subject)
            conditions.append(f"a.subject = ${len(params)}")

        rows = await conn.fetch(f'''
            SELECT
                a.id, a.mode, a.title, a.subject, a.exam_board, a.level,
                a.started_at, a.completed_at, a.time_limit_seconds, a.total_marks,
                COUNT(DISTINCT ans.question_id) AS question_count,
                COALESCE(SUM(ans.marks_awarded), 0) AS marks_earned,
                COALESCE(SUM(ans.marks_possible), 0) AS marks_possible
            FROM attempts a
            LEFT JOIN attempt_questions aq ON aq.attempt_id = a.id
            LEFT JOIN LATERAL (
                SELECT DISTINCT ON (question_id) question_id, marks_awarded, marks_possible
                FROM answers
                WHERE attempt_id = a.id AND question_id = aq.question_id
                ORDER BY question_id, created_at DESC
            ) ans ON true
            WHERE {' AND '.join(conditions)}
            GROUP BY a.id
            ORDER BY a.started_at DESC
        ''', *params)

        return [dict(r) for r in rows]


@router.get("/attempts/{attempt_id}")
async def get_attempt_detail(
    attempt_id: str,
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    async with db.acquire() as conn:
        attempt = await conn.fetchrow(
            'SELECT * FROM attempts WHERE id = $1 AND user_id = $2', attempt_id, user_id
        )
        if not attempt:
            raise HTTPException(status_code=404, detail="Attempt not found")

        rows = await conn.fetch('''
            SELECT q.*, p.exam_board, p.subject, p.paper_code, st.spec_code, st.title as topic_title
            FROM attempt_questions aq
            JOIN questions q ON q.id = aq.question_id
            JOIN papers p ON q.paper_id = p.id
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            WHERE aq.attempt_id = $1
            ORDER BY aq.position ASC
        ''', attempt_id)

        questions = await _attach_answers_for_attempt(conn, attempt_id, rows)

        # A custom paper's questions were shown to the student under
        # sequential display numbers (1.1, 1.2, 2.1...) rather than their
        # original bank question_number - purely a rendering choice made
        # once at generation time (_renumber_for_custom_paper), never
        # persisted anywhere. Reproduce the identical renumbering here so
        # History/Review shows the same numbers the student actually saw,
        # instead of the original source-paper numbers ("06.1", "01.1")
        # they never cross-reference against. This is deterministic and
        # reproduces the exact original numbering because attempt_questions
        # was recorded in this same position order right after renumbering.
        if attempt['mode'] == 'custom':
            questions = _renumber_for_custom_paper(questions)

        return {
            "attempt": dict(attempt),
            "questions": questions
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
            # See adaptive queue's matching comment above - a question can
            # cover more than one spec topic, so match against ANY of its
            # topics via question_topics rather than only its primary
            # q.spec_topic_id.
            params.append(req.spec_topic_ids)
            conditions.append(
                f"EXISTS (SELECT 1 FROM question_topics qt WHERE qt.question_id = q.id AND qt.spec_topic_id = ANY(${len(params)}))"
            )

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

        total_marks = sum(q['mark_value'] for q in questions)
        if configured_time_limit_minutes:
            time_limit_seconds = configured_time_limit_minutes * 60
        else:
            time_limit_seconds = max(total_marks * DEFAULT_SECONDS_PER_MARK, MIN_CUSTOM_PAPER_TIME_LIMIT_SECONDS)

        title = f"{req.subject} Mock Paper"
        attempt_id = await conn.fetchval('''
            INSERT INTO attempts (
                user_id, paper_id, source, mode, title, subject, exam_board, level,
                time_limit_seconds, total_marks
            )
            VALUES ($1, NULL, 'custom_generated', 'custom', $2, $3, $4, $5, $6, $7)
            RETURNING id
        ''', user_id, title, req.subject, req.exam_board, req.level, time_limit_seconds, total_marks)
        await _record_attempt_questions(conn, attempt_id, [q['id'] for q in questions])

        return {
            "attempt_id": str(attempt_id),
            "title": title,
            "questions": questions,
            "total_marks": total_marks,
            "target_marks": target_marks,
            "time_limit_seconds": time_limit_seconds
        }
