import os
import asyncpg
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/acexam")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

pg_pool = None
redis_client = None

async def init_db():
    global pg_pool, redis_client
    try:
        pg_pool = await asyncpg.create_pool(DATABASE_URL)
    except Exception as e:
        print(f"Failed to connect to Postgres: {e}")
        
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
    except Exception as e:
        print(f"Failed to connect to Redis: {e}")
        redis_client = None

async def close_db():
    global pg_pool, redis_client
    if pg_pool:
        await pg_pool.close()
    if redis_client:
        await redis_client.close()

def get_db():
    return pg_pool

def get_redis():
    return redis_client
