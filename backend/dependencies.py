import logging
import os
import secrets
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Request, HTTPException, status, Depends

logger = logging.getLogger(__name__)

# Known-insecure defaults that have appeared as JWT_SECRET fallbacks in this
# codebase (this file's own previous hardcoded default, and docker-compose.yml's
# fallback). Anyone who copy-pasted one of these without changing it gets a
# loud warning at startup.
_KNOWN_INSECURE_SECRETS = {
    "supersecretkey_for_dev_only",
    "dev_secret_change_me",
    "changeme",
    "secret",
}

_env_secret = os.getenv("JWT_SECRET")

if _env_secret is None:
    # No JWT_SECRET configured at all. Never fall back to a fixed, publicly
    # known string (that's how a security audit forged an admin token in
    # acexam#13). Instead generate a random, unguessable secret for this
    # process only, so zero-config local dev keeps working but a real
    # deployment that forgot to set JWT_SECRET doesn't get a guessable one.
    # (Trade-off: existing sessions are invalidated on every process restart -
    # acceptable, and actually desirable, for a dev-only fallback.)
    SECRET_KEY = secrets.token_hex(32)
    logger.warning(
        "JWT_SECRET not set - using a random ephemeral secret; set JWT_SECRET "
        "explicitly for any deployment where tokens must survive a restart."
    )
elif _env_secret in _KNOWN_INSECURE_SECRETS:
    # JWT_SECRET was set, but to a well-known placeholder value - someone
    # copy-pasted a default without changing it. There's no environment-tier
    # flag anywhere in this codebase (checked main.py/database.py) to safely
    # distinguish dev from prod here, so we can't hard-fail without risking
    # breaking a legitimate local setup - warn loudly instead.
    SECRET_KEY = _env_secret
    logger.warning(
        "JWT_SECRET is set to a known default/placeholder value (%r). "
        "This is insecure for any non-local deployment - set JWT_SECRET to a "
        "unique, random value.",
        _env_secret,
    )
else:
    SECRET_KEY = _env_secret

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 1 week

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_user_id(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return user_id
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

async def get_current_admin_id(request: Request):
    from database import get_db
    user_id = get_current_user_id(request)

    db = get_db()
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    async with db.acquire() as conn:
        is_admin = await conn.fetchval("SELECT is_admin FROM users WHERE id = $1", user_id)

    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    return user_id

async def rate_limit(request: Request, limit: int = 60, window: int = 60):
    from database import get_redis
    redis_client = get_redis()
    if not redis_client:
        return # Skip rate limit if redis is not available
    
    # We could use user_id or IP
    user_id = "anonymous"
    try:
        user_id = get_current_user_id(request)
    except HTTPException:
        # Fallback to IP
        user_id = request.client.host if request.client else "unknown_ip"
        
    key = f"rate_limit:{user_id}"
    
    try:
        current_count = await redis_client.get(key)
        if current_count and int(current_count) >= limit:
            raise HTTPException(status_code=429, detail="You're going a bit fast - give it a moment and try again.")
        
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window, nx=True)
        await pipe.execute()
    except HTTPException:
        raise
    except Exception as e:
        # Graceful fallback: do not block legitimate requests if Redis transiently fails
        pass
