import os
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Request, HTTPException, status, Depends

SECRET_KEY = os.getenv("JWT_SECRET", "supersecretkey_for_dev_only")
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
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window, nx=True)
        await pipe.execute()
    except HTTPException:
        raise
    except Exception as e:
        # Graceful fallback: do not block legitimate requests if Redis transiently fails
        pass
