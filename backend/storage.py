"""
S3-compatible object storage (MinIO locally, any real S3-compatible service
in prod) for everything that used to live on the backend container's local
disk - extracted question images and the original uploaded PDFs. The
backend has 2 replicas behind Traefik (see docker-compose.yml); a shared
docker volume worked for that (both replicas mounted the same
backend_media), but ties the app to a single host's disk and doesn't
survive a real multi-host deployment. An S3-compatible bucket both replicas
(and any future replica) talk to over the network removes that constraint
entirely - the backend container needs no volume for media at all.

The bucket is private (see docker-compose.yml's minio-init) - this content
is exam-board copyrighted past-paper material, not something to leave
world-readable at a guessable URL. Browsers never talk to S3 directly;
upload_bytes returns a path under this backend's own /api/media proxy
(routers/media.py), which enforces login (and admin-only for the original
source PDFs) before streaming the object through get_bytes below.

boto3 is a blocking/sync client; every call here goes through
asyncio.to_thread so it never blocks the event loop the way calling it
directly from an async route would.
"""
import asyncio
import os
from functools import lru_cache

import boto3
from botocore.client import Config

S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "acexam")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "acexam123")
S3_BUCKET = os.getenv("S3_BUCKET", "acexam-media")

MEDIA_PROXY_PREFIX = "/api/media"


@lru_cache(maxsize=1)
def _client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


async def upload_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Uploads bytes under `key` in the (private) bucket and returns the
    backend-proxied path a browser/frontend should use as the resource's
    src/href - never a direct S3 URL, since the bucket has no anonymous
    read access."""
    await asyncio.to_thread(
        _client().put_object, Bucket=S3_BUCKET, Key=key, Body=data, ContentType=content_type
    )
    return f"{MEDIA_PROXY_PREFIX}/{key}"


async def get_bytes(key: str) -> tuple[bytes, str]:
    """Fetches an object's bytes + content type for routers/media.py to
    stream back to an already-authorized caller."""
    obj = await asyncio.to_thread(_client().get_object, Bucket=S3_BUCKET, Key=key)
    body = await asyncio.to_thread(obj["Body"].read)
    return body, obj.get("ContentType", "application/octet-stream")
