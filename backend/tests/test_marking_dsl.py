"""
Unit tests for the deterministic marking DSL (marking_engine.evaluate_dsl_expression
and validate_dsl_syntax). These operators are what actually scores every 1-2 mark
question a student submits, and previously had no automated coverage - several of
the cases here reproduce bugs documented in marking_engine.py's own comments
(e.g. the OR/NOT-CONTAIN trap, MARKS-weighted partial credit) so they can't regress
silently.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marking_engine import evaluate_dsl_expression, validate_dsl_syntax


def test_contain_pass_and_fail():
    marks, _, missed = evaluate_dsl_expression("CONTAIN:mitochondria", "the mitochondria produces energy", mark_value=1)
    assert marks == 1
    assert missed == []

    marks, _, missed = evaluate_dsl_expression("CONTAIN:mitochondria", "the nucleus controls the cell", mark_value=1)
    assert marks == 0
    assert missed


def test_not_contain():
    marks, _, _ = evaluate_dsl_expression("NOT CONTAIN:wrong", "a correct answer", mark_value=1)
    assert marks == 1

    marks, _, _ = evaluate_dsl_expression("NOT CONTAIN:wrong", "this is wrong", mark_value=1)
    assert marks == 0


def test_any_and_all():
    marks, _, _ = evaluate_dsl_expression("ANY:cat,dog,bird", "I have a dog", mark_value=1)
    assert marks == 1

    marks, _, _ = evaluate_dsl_expression("ALL:cat,dog", "I have a dog", mark_value=1)
    assert marks == 0

    marks, _, _ = evaluate_dsl_expression("ALL:cat,dog", "I have a cat and a dog", mark_value=1)
    assert marks == 1


def test_min_at_least_n_of():
    dsl = "MIN:2:reflection,refraction,diffraction,absorption"
    marks, _, _ = evaluate_dsl_expression(dsl, "light shows reflection and diffraction", mark_value=2)
    assert marks == 2

    marks, _, _ = evaluate_dsl_expression(dsl, "light shows reflection", mark_value=2)
    assert marks == 0


def test_exact_numeric_and_algebraic():
    marks, _, _ = evaluate_dsl_expression("EXACT:108", "108", mark_value=3)
    assert marks == 3

    marks, _, _ = evaluate_dsl_expression("EXACT:108", "107.999996", mark_value=3)
    assert marks == 3

    marks, _, _ = evaluate_dsl_expression("EXACT:(x+2)/(x-3)", "(2+x)/(x-3)", mark_value=1)
    assert marks == 1


def test_range():
    marks, _, _ = evaluate_dsl_expression("RANGE:5,10", "the answer is 7", mark_value=1)
    assert marks == 1

    marks, _, _ = evaluate_dsl_expression("RANGE:5,10", "the answer is 12", mark_value=1)
    assert marks == 0


def test_mcq():
    options = [{"key": "A", "text": "Mitosis"}, {"key": "B", "text": "Meiosis"}]
    marks, _, _ = evaluate_dsl_expression("MCQ:B", "B", answer_options=options, mark_value=1)
    assert marks == 1

    marks, _, _ = evaluate_dsl_expression("MCQ:B", "Meiosis", answer_options=options, mark_value=1)
    assert marks == 1

    marks, _, _ = evaluate_dsl_expression("MCQ:B", "A", answer_options=options, mark_value=1)
    assert marks == 0


def test_and_or_composition():
    dsl = "CONTAIN:energy AND CONTAIN:mitochondria"
    marks, _, _ = evaluate_dsl_expression(dsl, "the mitochondria releases energy", mark_value=2)
    assert marks == 2

    marks, _, _ = evaluate_dsl_expression(dsl, "the mitochondria is round", mark_value=2)
    assert marks == 0

    dsl_or = "CONTAIN:mitochondria OR CONTAIN:powerhouse"
    marks, _, _ = evaluate_dsl_expression(dsl_or, "it is the powerhouse of the cell", mark_value=1)
    assert marks == 1


def test_unrecognized_operator_fails_closed():
    marks, _, missed = evaluate_dsl_expression("BOGUS:whatever", "whatever", mark_value=1)
    assert marks == 0
    assert missed


def test_marks_weighted_alternate_branch_never_exceeds_full_credit():
    # Real case documented in marking_engine.py: a 3-mark question where an
    # alternate (wrong-percentage) answer is only worth 1 mark - a plain OR
    # here would incorrectly award full marks to the alternate.
    dsl = "EXACT:108 OR MARKS:1:EXACT:7.2"
    marks, _, _ = evaluate_dsl_expression(dsl, "108", mark_value=3)
    assert marks == 3

    marks, _, _ = evaluate_dsl_expression(dsl, "7.2", mark_value=3)
    assert marks == 1

    marks, _, _ = evaluate_dsl_expression(dsl, "nonsense", mark_value=3)
    assert marks == 0


def test_marks_weight_capped_at_mark_value():
    # A MARKS:n weight higher than the question's mark_value must never award
    # more than mark_value - guards against a bad compilation overpaying.
    dsl = "MARKS:5:CONTAIN:anything"
    marks, _, _ = evaluate_dsl_expression(dsl, "anything goes here", mark_value=2)
    assert marks == 2


def test_validate_dsl_syntax_flags_unknown_operator():
    problems = validate_dsl_syntax("FOO:bar")
    assert any("Unknown operator" in p for p in problems)


def test_validate_dsl_syntax_flags_unbalanced_parens():
    problems = validate_dsl_syntax("(CONTAIN:a AND CONTAIN:b")
    assert any("Unbalanced" in p for p in problems)


def test_validate_dsl_syntax_flags_pure_not_contain_or_branch():
    # Documented real bug: an OR branch of only NOT CONTAIN clauses makes the
    # whole OR trivially pass for nearly any answer.
    problems = validate_dsl_syntax("CONTAIN:mitosis OR NOT CONTAIN:gamete")
    assert any("NOT CONTAIN" in p for p in problems)


def test_validate_dsl_syntax_accepts_well_formed_expression():
    problems = validate_dsl_syntax("CONTAIN:energy AND (CONTAIN:mitochondria OR CONTAIN:cell)")
    assert problems == []
