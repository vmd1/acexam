"""
One-off script: flag already-ingested questions whose marking_dsl is likely
wrong today, so they surface in the admin review queue instead of silently
mis-marking students. Run manually, requires DATABASE_URL (see migrate.py).

Two independent checks:
1. marking_engine.validate_dsl_syntax() against every stored marking_dsl -
   catches structurally broken DSL (operator keywords leaking into another
   clause's value, NOT CONTAIN used as a standalone OR branch, etc).
2. mark_scheme_text matching an "any N from" pattern while marking_dsl
   doesn't already use MIN: - these currently compile to plain ANY:, which
   awards full marks for matching just one of the N required points.

Only sets needs_review = true; never modifies marking_dsl or touches live
grading. Safe to re-run.
"""
import asyncio
import os
import re
import asyncpg
from dotenv import load_dotenv

import marking_engine

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

_ANY_N_FROM_RE = re.compile(r'\bany\s+(two|three|four|five|2|3|4|5)\s+from\b', re.IGNORECASE)


async def flag_dsl_issues():
    print(f"Connecting to {DATABASE_URL}...")
    conn = await asyncpg.connect(DATABASE_URL)

    rows = await conn.fetch(
        "SELECT id, marking_dsl, mark_scheme_text FROM questions "
        "WHERE marking_dsl IS NOT NULL AND marking_dsl != '' AND needs_review = false"
    )
    print(f"Checking {len(rows)} questions with a marking_dsl...")

    to_flag = []
    for row in rows:
        reasons = []

        problems = marking_engine.validate_dsl_syntax(row["marking_dsl"])
        if problems:
            reasons.append(f"DSL validation: {'; '.join(problems)}")

        scheme = row["mark_scheme_text"] or ""
        if _ANY_N_FROM_RE.search(scheme) and "MIN:" not in row["marking_dsl"]:
            reasons.append('mark scheme reads "any N from" but marking_dsl has no MIN: threshold operator')

        if reasons:
            to_flag.append((row["id"], reasons))

    print(f"Flagging {len(to_flag)} question(s) for review:")
    for qid, reasons in to_flag:
        print(f"  {qid}: {reasons}")

    if to_flag:
        await conn.executemany(
            "UPDATE questions SET needs_review = true WHERE id = $1",
            [(qid,) for qid, _ in to_flag],
        )

    print("Done.")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(flag_dsl_issues())
