import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

async def run_migrations():
    print(f"Connecting to {DATABASE_URL}...")
    conn = await asyncpg.connect(DATABASE_URL)
    
    with open('schema.sql', 'r') as f:
        schema_1 = f.read()
        
    with open('schema_phase2.sql', 'r') as f:
        schema_2 = f.read()
        
    with open('schema_phase3.sql', 'r') as f:
        schema_3 = f.read()

    with open('schema_phase4.sql', 'r') as f:
        schema_4 = f.read()

    print("Executing Phase 1 schema...")
    await conn.execute(schema_1)

    print("Executing Phase 2 schema...")
    await conn.execute(schema_2)

    print("Executing Phase 3 schema...")
    await conn.execute(schema_3)

    print("Executing Phase 4 schema...")
    await conn.execute(schema_4)
    
    print("Migrations complete!")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run_migrations())
