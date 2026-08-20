import asyncio
import asyncpg
import os
import json
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def seed_data():
    print(f"Connecting to {DATABASE_URL}...")
    conn = await asyncpg.connect(DATABASE_URL)
    
    # 1. Seed Spec Topics
    topics = [
        ("AQA", "Biology", "4.1.1", "Cell structure and microscopes"),
        ("AQA", "Biology", "4.1.2", "Cell division and mitosis"),
        ("AQA", "Biology", "4.2.1", "Principles of organisation"),
        ("AQA", "Biology", "4.2.2", "Human digestive and circulatory systems"),
        ("AQA", "Biology", "4.3.1", "Communicable diseases and immunity"),
        ("AQA", "Biology", "4.4.1", "Photosynthesis and rate limiting factors"),
        ("AQA", "Biology", "4.4.2", "Aerobic and anaerobic respiration"),
        ("AQA", "Biology", "4.5.1", "Homeostasis and nervous system"),
        ("AQA", "Biology", "4.6.1", "Reproduction and genetics"),
        ("AQA", "Biology", "4.7.1", "Ecosystems and adaptations"),
    ]
    
    topic_map = {}
    for board, subj, code, title in topics:
        tid = await conn.fetchval('''
            INSERT INTO spec_topics (exam_board, subject, spec_code, title)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING
            RETURNING id
        ''', board, subj, code, title)
        if not tid:
            tid = await conn.fetchval('SELECT id FROM spec_topics WHERE spec_code = $1', code)
        topic_map[code] = tid
        
    print(f"Seeded {len(topics)} spec topics.")
    
    # 2. Seed Misconception Taxonomy
    misconceptions = [
        ("4.1.2", "confuses_mitosis_meiosis", "Confuses mitosis and meiosis", "Mistakes cell division for growth with gamete production", True),
        ("4.4.2", "confuses_respiration_breathing", "Confuses respiration with breathing / ventilation", "Equates cellular respiration with mechanical inhalation/exhalation", True),
        ("4.2.2", "confuses_artery_vein_structure", "Confuses artery and vein structures", "Attributes valves or thin muscle walls to arteries", True),
        ("4.4.1", "light_intensity_inverse_square", "Misapplies inverse square law for light", "Fails to square distance when calculating relative light intensity", True),
        ("4.6.1", "dominant_recessive_allele_error", "Confuses dominant vs recessive alleles", "Assumes dominant alleles are more common or stronger physically", False)
    ]
    
    for spec, tag, label, desc, approved in misconceptions:
        await conn.execute('''
            INSERT INTO misconception_taxonomy (spec_code, tag_id, label, description, approved_at)
            VALUES ($1, $2, $3, $4, CASE WHEN $5 THEN now() ELSE NULL END)
            ON CONFLICT (spec_code, tag_id) DO NOTHING
        ''', spec, tag, label, desc, approved)
        
    print(f"Seeded {len(misconceptions)} misconception tags.")
    
    # 3. Seed Sample Published Past Paper
    paper_id = await conn.fetchval('''
        INSERT INTO papers (exam_board, subject, paper_code, series, source_pdf_url, status)
        VALUES ('AQA', 'Biology', '8461/1H', 'June 2023 Paper 1 Higher', 'https://cdn.acexam.app/papers/aqa-bio-2023-1h.pdf', 'published')
        RETURNING id
    ''')
    
    # 4. Seed Questions with Authentic layout & DSL / AI rubric
    questions = [
        {
            "num": "1(a)",
            "marks": 1,
            "text": "Name the cell structure where aerobic respiration takes place. [1 mark]",
            "type": "dsl",
            "dsl": "ANY:mitochondria,mitochondrion",
            "scheme": "Mitochondria / mitochondrion (allow phonetic spelling)",
            "spec": "4.4.2"
        },
        {
            "num": "1(b)",
            "marks": 2,
            "text": "State two differences between the processes of aerobic and anaerobic respiration in humans. [2 marks]",
            "type": "dsl",
            "dsl": "CONTAIN:oxygen AND (CONTAIN:lactic acid OR CONTAIN:energy)",
            "scheme": "Aerobic requires oxygen / produces more energy; anaerobic produces lactic acid / no oxygen.",
            "spec": "4.4.2"
        },
        {
            "num": "2(a)",
            "marks": 2,
            "text": "A student views a plant cell using a light microscope. The image size is 24 mm and the actual size is 0.08 mm. Calculate the magnification. [2 marks]",
            "type": "dsl",
            "dsl": "EXACT:300 OR RANGE:299.5,300.5",
            "scheme": "Magnification = Image / Actual = 24 / 0.08 = 300 (or x300).",
            "spec": "4.1.1"
        },
        {
            "num": "3(a)",
            "marks": 4,
            "text": "Explain how the human circulatory system is adapted to supply oxygen efficiently to exercising muscles. [4 marks]",
            "type": "ai",
            "dsl": None,
            "scheme": "* Double circulatory system maintains high blood pressure\n* Heart pumps blood faster / increased stroke volume\n* Arteries have thick elastic muscular walls to withstand pressure\n* Red blood cells contain haemoglobin and lack nucleus for high oxygen capacity\n* Capillaries have thin walls (one cell thick) for rapid diffusion distance",
            "spec": "4.2.2"
        },
        {
            "num": "4(a)",
            "marks": 4,
            "text": "Describe the stages of the cell cycle, including mitosis, and explain why mitosis is important for multicellular organisms. [4 marks]",
            "type": "ai",
            "dsl": None,
            "scheme": "* Stage 1 (Interphase): DNA replicates to form two copies of each chromosome and subcellular structures increase\n* Stage 2 (Mitosis): One set of chromosomes is pulled to each end of the cell and nucleus divides\n* Stage 3 (Cytokinesis): Cytoplasm and cell membranes divide to form two identical daughter cells\n* Importance: Growth, repair of damaged tissues, asexual reproduction",
            "spec": "4.1.2"
        }
    ]
    
    for q in questions:
        await conn.execute('''
            INSERT INTO questions (
                paper_id, question_number, mark_value, question_text,
                marking_type, marking_dsl, mark_scheme_text, spec_topic_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ''',
            paper_id, q["num"], q["marks"], q["text"],
            q["type"], q["dsl"], q["scheme"], topic_map.get(q["spec"])
        )
        
    print(f"Seeded sample past paper {paper_id} with {len(questions)} questions.")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(seed_data())
