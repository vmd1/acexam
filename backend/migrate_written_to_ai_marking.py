"""
One-off script: re-route already-ingested "written" answer_type questions
off the deterministic DSL and onto AI marking, matching the new policy in
ingestion.py (DSL is now reserved for numeric/select/multi_select/grid_select
answer types only - keyword/phrase-matching DSL operators proved too brittle
on free-text reasoning answers, false-negatively zeroing genuinely correct
paraphrased science). Without this, already-ingested written questions keep
their old marking_type = 'dsl' forever, since ingestion only decides
marking_type once, at upload time.

Sets marking_type = 'ai' and clears marking_dsl (no longer used for these
rows) on every question where answer_type = 'written' (or NULL, the legacy
default) and marking_type = 'dsl'. Never touches numeric/select/multi_select/
grid_select/practical questions. Safe to re-run.

Run manually, requires DATABASE_URL (see migrate.py).
"""
import asyncio
import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


async def migrate_written_to_ai_marking():
    print(f"Connecting to {DATABASE_URL}...")
    conn = await asyncpg.connect(DATABASE_URL)

    rows = await conn.fetch('''
        SELECT id FROM questions
        WHERE marking_type = 'dsl'
          AND (answer_type IS NULL OR answer_type = 'written')
    ''')
    print(f"Re-routing {len(rows)} written question(s) from DSL to AI marking...")

    if rows:
        await conn.execute('''
            UPDATE questions
            SET marking_type = 'ai', marking_dsl = NULL
            WHERE marking_type = 'dsl'
              AND (answer_type IS NULL OR answer_type = 'written')
        ''')

    print("Done.")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate_written_to_ai_marking())
