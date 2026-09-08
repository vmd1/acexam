from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from database import get_db
from dependencies import rate_limit, get_current_user_id
from profile_engine import calculate_decayed_mastery_scores

router = APIRouter(dependencies=[Depends(rate_limit)])

@router.get("/profile")
async def get_analytics_profile(
    subject: Optional[str] = None,
    exam_board: Optional[str] = None,
    level: Optional[str] = None,
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    # Analytics is viewed per-subject (from that subject's practice hub
    # page) - when subject/exam_board/level are given, every section below
    # is scoped to just that qualification instead of the student's whole
    # account.
    scoped = bool(subject and exam_board and level)

    async with db.acquire() as conn:
        await calculate_decayed_mastery_scores(conn, user_id)

        # 1. Topic Mastery Tree - scoped to the student's own subjects, so a
        # board/subject the student never added (e.g. Chemistry when they
        # only take Biology) doesn't show up as a 0%-mastery block; further
        # narrowed to the one subject being viewed when given.
        if scoped:
            topics = await conn.fetch('''
                SELECT st.id, st.spec_code, st.title, st.subject, st.exam_board, st.parent_id,
                       COALESCE(stm.mastery_score, 0.0) as mastery_score,
                       COALESCE(stm.decay_score, 0.0) as decay_score,
                       COALESCE(stm.attempts_count, 0) as attempts_count,
                       stm.last_practiced_at
                FROM spec_topics st
                LEFT JOIN student_topic_mastery stm ON st.id = stm.spec_topic_id AND stm.user_id = $1
                WHERE st.subject = $2 AND st.exam_board = $3 AND st.level = $4
                ORDER BY st.spec_code ASC
            ''', user_id, subject, exam_board, level)
        else:
            topics = await conn.fetch('''
                SELECT st.id, st.spec_code, st.title, st.subject, st.exam_board, st.parent_id,
                       COALESCE(stm.mastery_score, 0.0) as mastery_score,
                       COALESCE(stm.decay_score, 0.0) as decay_score,
                       COALESCE(stm.attempts_count, 0) as attempts_count,
                       stm.last_practiced_at
                FROM spec_topics st
                LEFT JOIN student_topic_mastery stm ON st.id = stm.spec_topic_id AND stm.user_id = $1
                WHERE EXISTS (
                    SELECT 1 FROM user_subjects us
                    WHERE us.user_id = $1
                      AND us.subject = st.subject
                      AND us.exam_board = st.exam_board
                      AND us.level = st.level
                )
                ORDER BY st.spec_code ASC
            ''', user_id)

        topic_spec_codes = {t['spec_code'] for t in topics}

        # 2. Command Word Competency Matrix - the schema tracks this per
        # command word only, with no subject dimension, so it can't be
        # narrowed to one subject; it's always account-wide.
        command_words = await conn.fetch('''
            SELECT command_word, marks_awarded, marks_possible,
                   CASE WHEN marks_possible > 0 THEN ROUND((marks_awarded::numeric / marks_possible) * 100, 1) ELSE 0 END as accuracy_pct
            FROM student_command_word_mastery
            WHERE user_id = $1
            ORDER BY marks_possible DESC
        ''', user_id)

        # 3. Misconceptions - narrowed to the spec codes belonging to this
        # subject when scoped (misconceptions are keyed by spec_code, which
        # spec_topics also uses, so this is the same identifier space).
        misconceptions = await conn.fetch('''
            SELECT sm.spec_code, sm.tag_id, sm.occurrences, sm.consecutive_correct, sm.status, sm.last_seen_at,
                   mt.label, mt.description
            FROM student_misconceptions sm
            LEFT JOIN misconception_taxonomy mt ON sm.spec_code = mt.spec_code AND sm.tag_id = mt.tag_id
            WHERE sm.user_id = $1
            ORDER BY sm.status ASC, sm.occurrences DESC
        ''', user_id)
        if scoped:
            misconceptions = [m for m in misconceptions if m['spec_code'] in topic_spec_codes]

        # 4. Global statistics - exclude answers still awaiting AI marking
        # ('pending_model': marks_awarded is NULL) so a question stuck in
        # the review queue doesn't drag accuracy down as a scored zero.
        # Deduplicated to the most recent answer per question (matching how
        # /generate/attempts computes a paper's own total) - summing every
        # raw answers row would double-count a question's marks_possible
        # (and marks_awarded) each time it was resubmitted/reattempted,
        # which is what produced a different "marks possible" total here
        # than the same paper showed on the History page.
        if scoped:
            stats = await conn.fetchrow('''
                SELECT COUNT(*) as total_answers,
                       COALESCE(SUM(marks_awarded), 0) as total_marks_earned,
                       COALESCE(SUM(marks_possible), 0) as total_marks_possible
                FROM (
                    SELECT DISTINCT ON (a.question_id) a.marks_awarded, a.marks_possible
                    FROM answers a
                    JOIN questions q ON a.question_id = q.id
                    JOIN papers p ON q.paper_id = p.id
                    WHERE a.user_id = $1 AND a.marked_by != 'pending_model'
                      AND p.subject = $2 AND p.exam_board = $3 AND p.level = $4
                    ORDER BY a.question_id, a.created_at DESC
                ) latest
            ''', user_id, subject, exam_board, level)
        else:
            stats = await conn.fetchrow('''
                SELECT COUNT(*) as total_answers,
                       COALESCE(SUM(marks_awarded), 0) as total_marks_earned,
                       COALESCE(SUM(marks_possible), 0) as total_marks_possible
                FROM (
                    SELECT DISTINCT ON (question_id) marks_awarded, marks_possible
                    FROM answers
                    WHERE user_id = $1 AND marked_by != 'pending_model'
                    ORDER BY question_id, created_at DESC
                ) latest
            ''', user_id)

        total_answers = stats['total_answers'] if stats else 0
        total_earned = stats['total_marks_earned'] if stats else 0
        total_possible = stats['total_marks_possible'] if stats else 0
        overall_pct = (total_earned / total_possible * 100) if total_possible > 0 else 0

        # Predicted grade - looked up from grade_boundaries, which are
        # admin-configured per (exam_board, level, subject, tier) since real
        # boundaries differ per qualification and per tier (e.g. AQA GCSE
        # Biology Higher vs Foundation). Only meaningful when viewing a
        # single scoped qualification - there's no one boundary set that
        # applies across a student's whole account.
        predicted_grade = "N/A"
        if scoped and total_possible >= 10:
            tier_row = await conn.fetchrow('''
                SELECT tier FROM user_subjects
                WHERE user_id = $1 AND subject = $2 AND exam_board = $3 AND level = $4
            ''', user_id, subject, exam_board, level)
            tier = (tier_row['tier'] if tier_row else None) or ''
            # NULL-safe tier comparison: grade_boundaries.tier is NOT NULL
            # DEFAULT '' (the untiered convention), but user_subjects.tier is
            # a plain nullable TEXT column, so a naive `tier = $4` here would
            # silently match zero rows the moment either side ends up NULL
            # instead of '' (ordinary SQL equality never matches NULL,
            # including NULL = ''). IS NOT DISTINCT FROM is Postgres's
            # null-safe equality operator, so this stays correct regardless
            # of which convention a given row (or the Python coercion above)
            # actually used.
            boundaries = await conn.fetch('''
                SELECT grade, min_pct FROM grade_boundaries
                WHERE exam_board = $1 AND level = $2 AND subject = $3
                  AND tier IS NOT DISTINCT FROM $4
                ORDER BY min_pct DESC
            ''', exam_board, level, subject, tier)
            for b in boundaries:
                if overall_pct >= b['min_pct']:
                    predicted_grade = b['grade']
                    break

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
