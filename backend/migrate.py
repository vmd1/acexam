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

    with open('schema_phase5.sql', 'r') as f:
        schema_5 = f.read()

    with open('schema_phase6.sql', 'r') as f:
        schema_6 = f.read()

    with open('schema_phase7.sql', 'r') as f:
        schema_7 = f.read()

    with open('schema_phase8.sql', 'r') as f:
        schema_8 = f.read()

    with open('schema_phase9.sql', 'r') as f:
        schema_9 = f.read()

    with open('schema_phase10.sql', 'r') as f:
        schema_10 = f.read()

    with open('schema_phase11.sql', 'r') as f:
        schema_11 = f.read()

    with open('schema_phase12.sql', 'r') as f:
        schema_12 = f.read()

    print("Executing Phase 1 schema...")
    await conn.execute(schema_1)

    print("Executing Phase 2 schema...")
    await conn.execute(schema_2)

    print("Executing Phase 3 schema...")
    await conn.execute(schema_3)

    print("Executing Phase 4 schema...")
    await conn.execute(schema_4)

    print("Executing Phase 5 schema...")
    await conn.execute(schema_5)

    print("Executing Phase 6 schema...")
    await conn.execute(schema_6)

    print("Executing Phase 7 schema...")
    await conn.execute(schema_7)

    print("Executing Phase 8 schema...")
    await conn.execute(schema_8)

    print("Executing Phase 9 schema...")
    await conn.execute(schema_9)

    print("Executing Phase 10 schema...")
    await conn.execute(schema_10)

    print("Executing Phase 11 schema...")
    await conn.execute(schema_11)

    print("Executing Phase 12 schema...")
    await conn.execute(schema_12)

    print("Migrations complete!")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run_migrations())
