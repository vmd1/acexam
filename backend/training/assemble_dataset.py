"""
§6.5 training-set assembly: pulls the accepted `training_examples` corpus
(§6.2) out of Postgres and writes it as mlx-lm chat-format JSONL, one
directory per spec code (here: exam_board/level/subject/tier, the actual
unit a marking model is trained per - §6.1).

Held out by paper series, not randomly (§6.5): the most recent series for
a spec code becomes test.jsonl in full, so held-out eval measures
generalisation to an unseen series rather than memorisation of a shuffled
subset. A small slice is further carved off the remaining (training)
series for valid.jsonl, since mlx_lm.lora tracks validation loss during
training.

The prompt shape mirrors marking_engine.py::mark_question's AI-marking
path (system_prompt / user_prompt) exactly, so a fine-tuned adapter never
sees an input shape it wasn't trained on.
"""
import asyncio
import json
import os
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

import asyncpg

# backend/ (parent of this training/ dir) holds marking_prompt.py - the
# single source of truth for the system prompt, shared with
# marking_engine.py::mark_question (live serving) and ai_pipeline.py's
# synthetic-answer grader. This mirrors agent.py's identical sys.path trick
# for importing spec_code_utils from the same location.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from marking_prompt import build_system_prompt, prompt_hash  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
DATA_ROOT = Path(__file__).parent / "data"
VALID_FRACTION = 0.1
TEST_FRACTION_SINGLE_SERIES = 0.1  # only used in the single-series fallback below
SEED = 42

# Mirrors marking_engine.py::mark_question's AI path exactly (system_prompt)
# - training and serving must never diverge (§6.5). Deliberately omits the
# live prompt's student-name personalization instruction: these examples
# are synthetic/examiner-exemplar answers with no real student attached, so
# there's no name to train the model to interpolate. Previously a
# hand-copied duplicate of marking_engine.py's string that had already
# drifted out of sync (an older, shorter consistency-rule wording) - now
# built from the same shared template so that can't happen again.
SYSTEM_PROMPT = build_system_prompt(None)
SYSTEM_PROMPT_HASH = prompt_hash()


def command_word_for(question_text):
    # Mirrors routers/feedback.py::submit_answer's fallback exactly (no
    # command_word was captured on training_examples rows, so we re-derive
    # it the same way inference does when the frontend doesn't supply one).
    first_word = question_text.strip().split()[0] if question_text else "Explain"
    return first_word.capitalize()


def series_root(series):
    # "June 2025 Paper 1 Higher Tier" -> "June 2025", so held-out splitting
    # groups by exam sitting, not by individual paper within that sitting.
    return re.sub(r"\s*Paper\s+\d+.*$", "", series or "", flags=re.I).strip() or (series or "unknown")


def qualification_key(row):
    return f"{row['exam_board']}-{row['level']}-{row['subject']}-{row['tier'] or 'none'}"


def slugify(key):
    return key.lower().replace(" ", "_").replace("/", "-")


def to_chat_example(row, known_by_spec):
    # Mirrors marking_engine.py::mark_question's known_misconceptions block
    # exactly - training and serving must show the model the same "approved
    # list" prompt shape, or a fine-tuned adapter learns to expect a tags
    # list that never actually appears at inference time.
    known = known_by_spec.get(row["spec_code"], [])
    known_ids = {t["tag_id"] for t in known}
    tags_list = (
        "\n".join(f"- {t['tag_id']}: {t['label']}" for t in known)
        if known else "(none approved yet for this specification)"
    )
    prompt = f"""
    Question: {row['question_text']}
    Total Marks: {row['mark_value']}
    Specification Code: {row['spec_code'] or 'General Science'}
    Command Word: {command_word_for(row['question_text'])}
    Student's first name: (unknown - address them as "you")
    Official Mark Scheme:
    \"\"\"{row['mark_scheme_text'] or 'Award marks for accurate scientific reasoning.'}\"\"\"

    Student Answer:
    \"\"\"{row['candidate_answer']}\"\"\"

    Approved misconception tags for this specification (choose from these only for misconception_tags):
    {tags_list}
    """
    raw_tags = json.loads(row["misconception_tags"]) if isinstance(row["misconception_tags"], str) else row["misconception_tags"]
    # Gold tags are filtered to the *current* approved list too - a training
    # example generated before a tag was approved (or since renamed/removed)
    # would otherwise teach the model to output a tag_id absent from its own
    # prompt's approved list, the same inconsistency this whole fix removes
    # from live marking.
    gold_tags = [t for t in (raw_tags or []) if t in known_ids]
    completion = json.dumps({
        "marks_awarded": row["awarded_marks"],
        "www": json.loads(row["www"]) if isinstance(row["www"], str) else row["www"],
        "ebi": json.loads(row["missed_points"]) if isinstance(row["missed_points"], str) else row["missed_points"],
        "misconception_tags": gold_tags,
        "new_tag_suggestion": None,
    })
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": completion},
        ]
    }


async def fetch_rows(conn):
    return await conn.fetch("""
        SELECT
            te.candidate_answer, te.awarded_marks, te.www,
            te.missed_points, te.misconception_tags, te.spec_code,
            q.question_text, q.mark_value, q.mark_scheme_text,
            p.exam_board, p.level, p.subject, p.tier, p.series
        FROM training_examples te
        JOIN questions q ON q.id = te.question_id
        JOIN papers p ON p.id = q.paper_id
        WHERE te.is_accepted_for_training = true
        ORDER BY p.series, q.question_number
    """)


def write_jsonl(path, examples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")


async def main():
    if not DATABASE_URL:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await fetch_rows(conn)
        taxonomy_rows = await conn.fetch(
            "SELECT spec_code, tag_id, label FROM misconception_taxonomy WHERE approved_at IS NOT NULL"
        )
    finally:
        await conn.close()

    known_by_spec = defaultdict(list)
    for t in taxonomy_rows:
        known_by_spec[t["spec_code"]].append({"tag_id": t["tag_id"], "label": t["label"]})

    by_qual = defaultdict(list)
    for row in rows:
        by_qual[qualification_key(row)].append(row)

    rng = random.Random(SEED)
    summary = {}
    for qual_key, qual_rows in by_qual.items():
        by_sitting = defaultdict(list)
        for row in qual_rows:
            by_sitting[series_root(row["series"])].append(row)

        if len(by_sitting) > 1:
            # Most recent sitting (lexicographic works here: "June 2024" <
            # "June 2025"), all papers within it, held out in full as the
            # unseen-series test set (§6.5).
            held_out_series = max(by_sitting.keys())
            test_rows = by_sitting.pop(held_out_series)
            train_pool_rows = [r for rs in by_sitting.values() for r in rs]
        else:
            # Only one exam series ingested so far for this qualification -
            # holding it out in full (the normal design above) would leave
            # zero training examples, which crashes mlx_lm.lora on an empty
            # train.jsonl (IndexError: list index out of range). Fall back
            # to a random split of this one series instead; test.jsonl won't
            # measure true unseen-series generalization until a second
            # series is ingested for this qualification.
            held_out_series = None
            only_series = next(iter(by_sitting))
            print(
                f"WARNING: only one series ({only_series}) for {qual_key} - "
                "falling back to a random train/valid/test split instead of "
                "a held-out series; ingest another series' papers for a "
                "real unseen-series eval.",
                file=sys.stderr,
            )
            all_rows = list(qual_rows)
            rng.shuffle(all_rows)
            n_test = min(int(len(all_rows) * TEST_FRACTION_SINGLE_SERIES), max(0, len(all_rows) - 1))
            test_rows = all_rows[:n_test]
            train_pool_rows = all_rows[n_test:]

        rng.shuffle(train_pool_rows)
        n_valid = min(int(len(train_pool_rows) * VALID_FRACTION), max(0, len(train_pool_rows) - 1))
        valid_rows = train_pool_rows[:n_valid]
        train_rows = train_pool_rows[n_valid:]

        slug = slugify(qual_key)
        out_dir = DATA_ROOT / slug
        write_jsonl(out_dir / "train.jsonl", [to_chat_example(r, known_by_spec) for r in train_rows])
        write_jsonl(out_dir / "valid.jsonl", [to_chat_example(r, known_by_spec) for r in valid_rows])
        write_jsonl(out_dir / "test.jsonl", [to_chat_example(r, known_by_spec) for r in test_rows])

        summary[qual_key] = {
            "held_out_series": held_out_series,
            "train": len(train_rows),
            "valid": len(valid_rows),
            "test": len(test_rows),
            "dir": str(out_dir),
        }

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
