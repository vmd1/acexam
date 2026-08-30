import asyncio
import asyncpg
import bcrypt
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
SEED_ADMIN_EMAIL = os.getenv("SEED_ADMIN_EMAIL", "admin@acexam.dev")
SEED_ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "AdminDev123!")

async def seed_data():
    print(f"Connecting to {DATABASE_URL}...")
    conn = await asyncpg.connect(DATABASE_URL)

    # 0. Seed a dev admin account (idempotent) so the ingestion/review
    # console is testable without manually flipping is_admin in the DB.
    admin_hash = bcrypt.hashpw(SEED_ADMIN_PASSWORD.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    await conn.execute('''
        INSERT INTO users (email, password_hash, display_name, is_admin)
        VALUES ($1, $2, 'Acexam Admin', true)
        ON CONFLICT (email) DO NOTHING
    ''', SEED_ADMIN_EMAIL, admin_hash)
    print(f"Ensured dev admin account exists: {SEED_ADMIN_EMAIL}")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(seed_data())
