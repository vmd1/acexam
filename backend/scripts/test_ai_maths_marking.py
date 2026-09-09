"""
Ad-hoc test: exercises marking_engine.mark_question's AI path directly
against real numeric ("Calculate") mark schemes from the freshly-ingested
AQA-84611H-QP-JUN25 paper, using the live self-hosted adapter for
AQA/GCSE/Biology/Higher. Not part of the app - one-off verification that
AI marking handles numeric/maths answers sensibly, and specifically that it
gets the Q01.6 partial-credit case right (the exact scenario
_has_partial_credit_alternative in ingestion.py now routes to this path
instead of DSL).

Usage: python test_ai_maths_marking.py
"""
import asyncio
import database
import marking_engine

CASES = [
    {
        "label": "Q01.6 - correct final answer (should be 3/3)",
        "question_text": "There were 240 children in the survey.\n\nCalculate how many unvaccinated children had a severe measles infection.\n\nUse Figure 1.",
        "mark_value": 3,
        "mark_scheme_text": (
            "- (graph reading) = 45\n"
            "- 45/100 x 240\n"
            "- 108\n"
            "- allow 0.45 x 240\n"
            "- allow for 1 mark an answer of 7 / 7.2 with evidence of having used 3(%) from Figure 1 "
            "or allow for 1 mark an answer of 88 / 88.8 / 89 with evidence of having used 37(%) from Figure 1"
        ),
        "spec_code": "4.3.1.2",
        "student_answer": "108",
    },
    {
        "label": "Q01.6 - wrong-method partial-credit answer (real scheme says 1/3, DSL would have given 3/3)",
        "question_text": "There were 240 children in the survey.\n\nCalculate how many unvaccinated children had a severe measles infection.\n\nUse Figure 1.",
        "mark_value": 3,
        "mark_scheme_text": (
            "- (graph reading) = 45\n"
            "- 45/100 x 240\n"
            "- 108\n"
            "- allow 0.45 x 240\n"
            "- allow for 1 mark an answer of 7 / 7.2 with evidence of having used 3(%) from Figure 1 "
            "or allow for 1 mark an answer of 88 / 88.8 / 89 with evidence of having used 37(%) from Figure 1"
        ),
        "spec_code": "4.3.1.2",
        "student_answer": "7.2 (I used 3% from the graph and calculated 3/100 x 240 = 7.2)",
    },
    {
        "label": "Q01.6 - way off / no working (should be 0/3)",
        "question_text": "There were 240 children in the survey.\n\nCalculate how many unvaccinated children had a severe measles infection.\n\nUse Figure 1.",
        "mark_value": 3,
        "mark_scheme_text": (
            "- (graph reading) = 45\n"
            "- 45/100 x 240\n"
            "- 108\n"
            "- allow 0.45 x 240\n"
            "- allow for 1 mark an answer of 7 / 7.2 with evidence of having used 3(%) from Figure 1 "
            "or allow for 1 mark an answer of 88 / 88.8 / 89 with evidence of having used 37(%) from Figure 1"
        ),
        "spec_code": "4.3.1.2",
        "student_answer": "20",
    },
    {
        "label": "Q05.2 - correct with rounding tolerance (should be full 5/5)",
        "question_text": "Figure 5 shows a red blood cell.\n\nThe image of the red blood cell in Figure 5 is magnified 7200 times.\nCalculate the real diameter of the red blood cell.\nGive your answer in micrometres (µm).",
        "mark_value": 5,
        "mark_scheme_text": (
            "- recall of equation: magnification = size of image / size of real object\n"
            "- rearrangement of equation: size of real object = size of image / magnification\n"
            "- substitution: 6.3/7200\n"
            "- 0.000875 (cm)\n"
            "- conversion: 8.75 (µm)\n"
            "- allow 8.8 (µm)"
        ),
        "spec_code": "4.1.1.5",
        "student_answer": "magnification = image size / real size, so real size = 6.3/7200 = 0.000875 cm = 8.75 micrometres",
    },
    {
        "label": "Q05.2 - correct value but no working shown, wrong unit stated (partial credit expected)",
        "question_text": "Figure 5 shows a red blood cell.\n\nThe image of the red blood cell in Figure 5 is magnified 7200 times.\nCalculate the real diameter of the red blood cell.\nGive your answer in micrometres (µm).",
        "mark_value": 5,
        "mark_scheme_text": (
            "- recall of equation: magnification = size of image / size of real object\n"
            "- rearrangement of equation: size of real object = size of image / magnification\n"
            "- substitution: 6.3/7200\n"
            "- 0.000875 (cm)\n"
            "- conversion: 8.75 (µm)\n"
            "- allow 8.8 (µm)"
        ),
        "spec_code": "4.1.1.5",
        "student_answer": "8.75 cm",
    },
]


async def main():
    # marking_engine.mark_question's AI path goes through ai_pipeline's
    # Redis job queue to the live self-hosted adapter (see
    # mark_with_selfhosted_model) - database.get_redis() only returns a real
    # client after database.init_db() runs, which normally happens in
    # main.py's FastAPI lifespan startup. This standalone script never goes
    # through that, so call it explicitly first or every call silently
    # degrades to the crude local keyword-overlap stub instead of the real
    # adapter.
    await database.init_db()
    for case in CASES:
        result = await marking_engine.mark_question(
            question_text=case["question_text"],
            mark_value=case["mark_value"],
            marking_type="ai",
            marking_dsl=None,
            mark_scheme_text=case["mark_scheme_text"],
            student_answer=case["student_answer"],
            command_word="Calculate",
            spec_code=case["spec_code"],
            ai_marking_status="live",
            exam_board="AQA",
            level="GCSE",
            subject="Biology",
            tier="Higher",
            known_misconceptions=[],
        )
        print("=" * 100)
        print(case["label"])
        print(f"  student answer: {case['student_answer']!r}")
        print(f"  marks_awarded: {result['marks_awarded']} / {case['mark_value']}  (marked_by={result['marked_by']})")
        print(f"  www: {result['www']}")
        print(f"  missed_points: {result['missed_points']}")
        print(f"  feedback_text:\n{result['feedback_text']}")
        print()
    await database.close_db()


if __name__ == "__main__":
    asyncio.run(main())
