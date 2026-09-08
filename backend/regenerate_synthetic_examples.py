"""
One-off backfill: regenerates `training_examples` rows with source='synthetic'
for a qualification, using the current (fixed) blind_grade_synthetic_answer
prompt - marking_engine.py's live system prompt and this grader used to
disagree on one thing: whether marks_awarded and www/ebi were allowed to
contradict each other (full marks with a non-empty ebi list). Both prompts
now state the same hard consistency rule, but the *existing* training corpus
was generated before that fix landed, so the already-fine-tuned model likely
learned the contradiction from its own training data. Re-running the
synthetic-answer generation + grading step (ingestion.py's
_samples_for_synthetic_generation gate, same deterministic per-question
sampling) with the corrected grader produces clean replacement examples for
the next training run - a fresh model still needs to be trained on this
output for the fix to actually reach live marking.

Does NOT touch source='examiner_exemplar' rows - those come from real
candidate answers extracted from the examiner report PDF, which isn't
stored anywhere after upload (see CLAUDE.md), so they can't be regenerated
without re-uploading the original paper.

Usage: DATABASE_URL=... python3 regenerate_synthetic_examples.py \
    --exam-board AQA --level GCSE --subject Biology --tier Higher
"""
import argparse
import asyncio
import json
import os

import asyncpg

import ai_pipeline
from ingestion import _samples_for_synthetic_generation


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exam-board", required=True)
    parser.add_argument("--level", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--tier", default="")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL not set")

    conn = await asyncpg.connect(database_url)
    try:
        known_topic_rows = await conn.fetch(
            "SELECT spec_code, title FROM spec_topics WHERE exam_board = $1 AND subject = $2 AND level = $3",
            args.exam_board, args.subject, args.level,
        )
        known_codes = [r["spec_code"] for r in known_topic_rows]
        known_misconceptions = []
        if known_codes:
            taxonomy_rows = await conn.fetch(
                "SELECT tag_id, label, approved_at FROM misconception_taxonomy WHERE spec_code = ANY($1)",
                known_codes,
            )
            known_misconceptions = [
                {"tag_id": r["tag_id"], "label": r["label"]}
                for r in taxonomy_rows if r["approved_at"] is not None
            ]

        questions = await conn.fetch(
            """
            SELECT q.id, q.question_text, q.mark_value, q.mark_scheme_text, st.spec_code AS topic_spec_code
            FROM questions q
            JOIN papers p ON p.id = q.paper_id
            LEFT JOIN spec_topics st ON q.spec_topic_id = st.id
            WHERE q.marking_type = 'ai'
              AND p.exam_board = $1 AND p.level = $2 AND p.subject = $3 AND p.tier = $4
            """,
            args.exam_board, args.level, args.subject, args.tier,
        )

        eligible = [q for q in questions if _samples_for_synthetic_generation(q["question_text"], q["mark_value"])]
        print(f"{len(eligible)} / {len(questions)} AI-marked questions eligible for synthetic regeneration")

        total_generated = 0
        total_accepted = 0
        for i, q in enumerate(eligible, 1):
            print(f"[{i}/{len(eligible)}] Q (mark_value={q['mark_value']}): {q['question_text'][:80]!r}...")
            result = await ai_pipeline.generate_and_validate_synthetic_answers(
                question_text=q["question_text"],
                mark_value=q["mark_value"],
                mark_scheme=q["mark_scheme_text"] or "Award marks for correct scientific reasoning.",
                spec_code=q["topic_spec_code"] or "",
                known_misconceptions=known_misconceptions,
            )
            examples = result["examples"]
            total_generated += len(examples)
            total_accepted += sum(1 for e in examples if e.get("is_accepted_for_training"))

            async with conn.transaction():
                deleted = await conn.fetchval(
                    "WITH d AS (DELETE FROM training_examples WHERE question_id = $1 AND source = 'synthetic' RETURNING 1) SELECT COUNT(*) FROM d",
                    q["id"],
                )
                if deleted:
                    print(f"  removed {deleted} stale synthetic example(s)")
                for ex in examples:
                    await conn.execute(
                        """
                        INSERT INTO training_examples (
                            question_id, spec_code, candidate_answer, target_marks,
                            awarded_marks, is_accepted_for_training, www,
                            missed_points, misconception_tags, source
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'synthetic')
                        """,
                        q["id"], ex.get("spec_code") or None, ex.get("student_answer", ""),
                        ex.get("target_marks", 0), ex.get("awarded_marks", 0),
                        bool(ex.get("is_accepted_for_training", False)),
                        json.dumps(ex.get("www", [])),
                        json.dumps(ex.get("missed_points", [])),
                        json.dumps(ex.get("misconception_tags", [])),
                    )
                for new_tag in result.get("proposed_tags", []):
                    await conn.execute(
                        """
                        INSERT INTO misconception_taxonomy (spec_code, tag_id, label, description, approved_at)
                        VALUES ($1, $2, $3, $4, NULL)
                        ON CONFLICT (spec_code, tag_id) DO NOTHING
                        """,
                        q["topic_spec_code"], new_tag.get("tag_id"), new_tag.get("label"), new_tag.get("description"),
                    )

        print(f"Done. Generated {total_generated} synthetic examples ({total_accepted} accepted) across {len(eligible)} questions.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
