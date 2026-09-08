"""
One-off script: rewrite already-ingested papers.source_pdf_url and
questions.images[].url from the old direct-S3 URL scheme
(http(s)://<S3_PUBLIC_URL host>/<bucket>/<key>) to the new backend-proxied
path (/api/media/<key>), matching storage.py's switch to a private bucket
(the direct URLs 404 now that the bucket no longer allows anonymous reads -
see routers/media.py for the auth-gated replacement). Only touches rows
whose stored value still looks like an absolute URL; already-proxied rows
(new ingestions) are left untouched. Safe to re-run.

Run manually, requires DATABASE_URL (see migrate.py).
"""
import asyncio
import json
import os
import re

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Matches e.g. "http://s3.acexam.localhost/acexam-media/papers/abc123.pdf"
# -> captures "papers/abc123.pdf". Host/bucket segment is whatever it was
# at upload time, so it's matched generically rather than against the
# current S3_PUBLIC_URL/S3_BUCKET env vars.
OLD_URL_RE = re.compile(r'^https?://[^/]+/[^/]+/(.+)$')


def to_proxy_path(url):
    if not url or not url.startswith(('http://', 'https://')):
        return None
    m = OLD_URL_RE.match(url)
    return f"/api/media/{m.group(1)}" if m else None


async def fix_media_urls():
    print(f"Connecting to {DATABASE_URL}...")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        papers = await conn.fetch("SELECT id, source_pdf_url FROM papers WHERE source_pdf_url LIKE 'http%'")
        paper_updates = 0
        for p in papers:
            new_url = to_proxy_path(p['source_pdf_url'])
            if new_url:
                await conn.execute("UPDATE papers SET source_pdf_url = $1 WHERE id = $2", new_url, p['id'])
                paper_updates += 1
        print(f"Rewrote {paper_updates} paper source_pdf_url(s)")

        questions = await conn.fetch("SELECT id, images FROM questions WHERE images IS NOT NULL AND images::text LIKE '%http%'")
        question_updates = 0
        for q in questions:
            imgs = json.loads(q['images']) if isinstance(q['images'], str) else (q['images'] or [])
            changed = False
            for img in imgs:
                new_url = to_proxy_path(img.get('url'))
                if new_url:
                    img['url'] = new_url
                    changed = True
            if changed:
                await conn.execute("UPDATE questions SET images = $1 WHERE id = $2", json.dumps(imgs), q['id'])
                question_updates += 1
        print(f"Rewrote images on {question_updates} question(s)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(fix_media_urls())
