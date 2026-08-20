import math
from datetime import datetime, timezone
import asyncpg
from typing import List, Dict, Any

EWMA_ALPHA = 0.35
MEMORY_STRENGTH_DAYS = 14.0 # S parameter in Ebbinghaus decay

async def update_student_profile_after_answer(
    conn: asyncpg.Connection,
    user_id: str,
    spec_topic_id: str | None,
    spec_code: str | None,
    command_word: str | None,
    marks_awarded: int,
    marks_possible: int,
    misconception_tags: List[str]
):
    """
    Updates all dimensions of the Master Student Profile:
    1. Topic Mastery Tree with EWMA
    2. Command Word Competency Matrix
    3. Persistent Misconception Memory
    """
    pct = (marks_awarded / marks_possible) if marks_possible > 0 else 0.0
    now = datetime.now(timezone.utc)
    
    # 1. Update Topic Mastery with EWMA
    if spec_topic_id:
        existing = await conn.fetchrow('''
            SELECT mastery_score, attempts_count FROM student_topic_mastery
            WHERE user_id = $1 AND spec_topic_id = $2
        ''', user_id, spec_topic_id)
        
        if existing:
            old_score = existing['mastery_score']
            new_score = (EWMA_ALPHA * pct) + ((1 - EWMA_ALPHA) * old_score)
            new_count = existing['attempts_count'] + 1
            await conn.execute('''
                UPDATE student_topic_mastery
                SET mastery_score = $1, attempts_count = $2, last_practiced_at = $3, decay_score = $1
                WHERE user_id = $4 AND spec_topic_id = $5
            ''', new_score, new_count, now, user_id, spec_topic_id)
        else:
            await conn.execute('''
                INSERT INTO student_topic_mastery (user_id, spec_topic_id, mastery_score, attempts_count, last_practiced_at, decay_score)
                VALUES ($1, $2, $3, 1, $4, $3)
            ''', user_id, spec_topic_id, pct, now)
            
    # 2. Update Command Word Competency Matrix
    if command_word:
        clean_cw = command_word.strip().capitalize()
        await conn.execute('''
            INSERT INTO student_command_word_mastery (user_id, command_word, marks_awarded, marks_possible, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, command_word)
            DO UPDATE SET
                marks_awarded = student_command_word_mastery.marks_awarded + EXCLUDED.marks_awarded,
                marks_possible = student_command_word_mastery.marks_possible + EXCLUDED.marks_possible,
                updated_at = EXCLUDED.updated_at
        ''', user_id, clean_cw, marks_awarded, marks_possible, now)
        
    # 3. Update Misconception Memory
    if spec_code:
        # Triggered misconceptions
        for tag in misconception_tags:
            await conn.execute('''
                INSERT INTO student_misconceptions (user_id, spec_code, tag_id, occurrences, consecutive_correct, status, last_seen_at)
                VALUES ($1, $2, $3, 1, 0, 'active', $4)
                ON CONFLICT (user_id, spec_code, tag_id)
                DO UPDATE SET
                    occurrences = student_misconceptions.occurrences + 1,
                    consecutive_correct = 0,
                    status = 'active',
                    last_seen_at = EXCLUDED.last_seen_at
            ''', user_id, spec_code, tag, now)
            
        # If full marks and no misconceptions triggered on this spec code, advance resolution counter
        if pct >= 0.9 and not misconception_tags:
            active_misc = await conn.fetch('''
                SELECT tag_id, consecutive_correct FROM student_misconceptions
                WHERE user_id = $1 AND spec_code = $2 AND status = 'active'
            ''', user_id, spec_code)
            
            for row in active_misc:
                new_streak = row['consecutive_correct'] + 1
                new_status = 'resolved' if new_streak >= 3 else 'active'
                await conn.execute('''
                    UPDATE student_misconceptions
                    SET consecutive_correct = $1, status = $2, last_seen_at = $3
                    WHERE user_id = $4 AND spec_code = $5 AND tag_id = $6
                ''', new_streak, new_status, now, user_id, spec_code, row['tag_id'])

async def calculate_decayed_mastery_scores(conn: asyncpg.Connection, user_id: str):
    """
    Recalculates Ebbinghaus memory decay score for all topics:
    Decay(t) = Mastery * e^(-(days_elapsed) / S)
    """
    rows = await conn.fetch('''
        SELECT spec_topic_id, mastery_score, last_practiced_at
        FROM student_topic_mastery
        WHERE user_id = $1
    ''', user_id)
    
    now = datetime.now(timezone.utc)
    for row in rows:
        last_dt = row['last_practiced_at']
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        days_elapsed = max(0.0, (now - last_dt).total_seconds() / 86400.0)
        decay = row['mastery_score'] * math.exp(-days_elapsed / MEMORY_STRENGTH_DAYS)
        await conn.execute('''
            UPDATE student_topic_mastery
            SET decay_score = $1
            WHERE user_id = $2 AND spec_topic_id = $3
        ''', decay, user_id, row['spec_topic_id'])
