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

    with open('schema_phase13.sql', 'r') as f:
        schema_13 = f.read()

    with open('schema_phase14.sql', 'r') as f:
        schema_14 = f.read()

    with open('schema_phase15.sql', 'r') as f:
        schema_15 = f.read()

    with open('schema_phase16.sql', 'r') as f:
        schema_16 = f.read()

    with open('schema_phase17.sql', 'r') as f:
        schema_17 = f.read()

    with open('schema_phase18.sql', 'r') as f:
        schema_18 = f.read()

    with open('schema_phase19.sql', 'r') as f:
        schema_19 = f.read()

    with open('schema_phase20.sql', 'r') as f:
        schema_20 = f.read()

    with open('schema_phase21.sql', 'r') as f:
        schema_21 = f.read()

    with open('schema_phase22.sql', 'r') as f:
        schema_22 = f.read()

    with open('schema_phase23.sql', 'r') as f:
        schema_23 = f.read()

    with open('schema_phase24.sql', 'r') as f:
        schema_24 = f.read()

    with open('schema_phase25.sql', 'r') as f:
        schema_25 = f.read()

    with open('schema_phase26.sql', 'r') as f:
        schema_26 = f.read()

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

    print("Executing Phase 13 schema...")
    await conn.execute(schema_13)

    print("Executing Phase 14 schema...")
    await conn.execute(schema_14)

    print("Executing Phase 15 schema...")
    await conn.execute(schema_15)

    print("Executing Phase 16 schema...")
    await conn.execute(schema_16)

    print("Executing Phase 17 schema...")
    await conn.execute(schema_17)

    print("Executing Phase 18 schema...")
    await conn.execute(schema_18)

    print("Executing Phase 19 schema...")
    await conn.execute(schema_19)

    print("Executing Phase 20 schema...")
    await conn.execute(schema_20)

    print("Executing Phase 21 schema...")
    await conn.execute(schema_21)

    print("Executing Phase 22 schema...")
    await conn.execute(schema_22)

    print("Executing Phase 23 schema...")
    await conn.execute(schema_23)

    print("Executing Phase 24 schema...")
    await conn.execute(schema_24)

    print("Executing Phase 25 schema...")
    await conn.execute(schema_25)

    print("Executing Phase 26 schema...")
    await conn.execute(schema_26)

    print("Migrations complete!")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run_migrations())
