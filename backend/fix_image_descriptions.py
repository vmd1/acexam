"""
One-off backfill: re-runs the dual-image-description agreement check with
real image bytes for papers ingested while generate_dual_image_descriptions
was silently reading from a local backend/media/ disk path that stopped
existing once image storage moved to S3/MinIO (see storage.py) - every
call ran blind (img_bytes always None), so both "independent" descriptions
were pure hallucination with nothing in common, and every single image
failed the similarity check and got flagged needs_review regardless of
whether anything was actually wrong with it. That flag then propagated to
every sub-question in a group (see ingestion.py's group-level image
linking), so this also inflated needs_review far beyond what the images
actually warranted.

Only touches the vision-description step - DSL, table_data, training
examples, and image/table group-linking are all unaffected by this bug and
not re-run here, unlike a full re-ingestion. Cheap: one HTTP fetch + two
vision calls per DISTINCT image (deduped by checksum across every question
that links it), not per question.

Usage: python fix_image_descriptions.py <paper_id> [<paper_id> ...]
Requires DATABASE_URL (see migrate.py).
"""
import asyncio
import json
import os
import sys

import asyncpg
import httpx
from dotenv import load_dotenv

import ai_pipeline
import marking_engine
import storage

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


async def fix_paper(conn: asyncpg.Connection, paper_id: str):
    rows = await conn.fetch(
        "SELECT id, images, marking_type, marking_dsl FROM questions WHERE paper_id = $1",
        paper_id
    )
    if not rows:
        print(f"  no questions found for paper {paper_id}")
        return

    # Dedupe images by checksum across every question that links them -
    # group-level linking means the same image dict (by value) appears on
    # several sibling rows, so re-describing it once and reusing the result
    # avoids redundant vision calls.
    images_by_checksum = {}
    for r in rows:
        imgs = json.loads(r["images"]) if isinstance(r["images"], str) else (r["images"] or [])
        for img in imgs:
            checksum = img.get("checksum")
            if checksum and checksum not in images_by_checksum:
                images_by_checksum[checksum] = img

    print(f"  {len(rows)} questions, {len(images_by_checksum)} distinct images")

    updated_fields_by_checksum = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for checksum, img in images_by_checksum.items():
            # img["url"] is the public S3_PUBLIC_URL (Traefik Host-header
            # routing, resolves fine for a browser/host process) - this
            # script runs inside the backend container, which has no DNS
            # record for that hostname. Fetch via the internal S3 endpoint
            # (same bucket/key, same network storage.py's own client uses)
            # instead.
            internal_url = f"{storage.S3_ENDPOINT_URL}/{storage.S3_BUCKET}/question-images/{checksum}.{img.get('ext', 'png')}"
            try:
                resp = await client.get(internal_url)
                resp.raise_for_status()
                img_bytes = resp.content
            except Exception as e:
                print(f"    failed to fetch {internal_url}: {e}")
                continue

            desc_a, desc_b, is_agreed, agreement_score = await ai_pipeline.generate_dual_image_descriptions(img, img_bytes=img_bytes)
            updated_fields_by_checksum[checksum] = {
                "description_run_a": desc_a,
                "description_run_b": desc_b,
                "is_description_agreed": is_agreed,
                "description_agreement_score": agreement_score,
                "description": desc_a,
                "needs_review": not is_agreed,
            }
            print(f"    {checksum[:12]}... agreed={is_agreed} score={agreement_score}")

    updated_count = 0
    for r in rows:
        imgs = json.loads(r["images"]) if isinstance(r["images"], str) else (r["images"] or [])
        changed = False
        any_image_needs_review = False
        for img in imgs:
            checksum = img.get("checksum")
            fields = updated_fields_by_checksum.get(checksum)
            if fields:
                img.update(fields)
                changed = True
            if img.get("needs_review"):
                any_image_needs_review = True

        if not changed:
            continue

        dsl_problems = marking_engine.validate_dsl_syntax(r["marking_dsl"]) if r["marking_type"] == "dsl" and r["marking_dsl"] else []
        needs_review = any_image_needs_review or bool(dsl_problems)

        await conn.execute(
            "UPDATE questions SET images = $1, needs_review = $2 WHERE id = $3",
            json.dumps(imgs), needs_review, r["id"]
        )
        updated_count += 1

    print(f"  updated {updated_count} questions")


async def main():
    if len(sys.argv) < 2:
        print("Usage: python fix_image_descriptions.py <paper_id> [<paper_id> ...]")
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for paper_id in sys.argv[1:]:
            print(f"Paper {paper_id}:")
            await fix_paper(conn, paper_id)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
