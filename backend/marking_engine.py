import re
import json
from typing import Dict, Any, List, Tuple, Optional
import sympy
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application,
    convert_xor,
)
import ai_pipeline
import marking_prompt
import table_utils
from spec_code_utils import spec_code_slug

# Only plain arithmetic/algebra characters are allowed through to sympy's parser.
# This blocks quotes, brackets, underscores etc. that could otherwise be used to
# reach Python builtins via sympy's eval-based expression parser.
_SAFE_MATH_RE = re.compile(r'^[0-9a-zA-Z\.\,\+\-\*\/\^\(\)\s]{1,200}$')
_MATH_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)


def _safe_math_globals() -> Dict[str, Any]:
    # Explicitly neuter builtins and only expose a small set of safe sympy names,
    # since eval() re-adds __builtins__ automatically if the key is absent.
    return {
        '__builtins__': {},
        'sqrt': sympy.sqrt,
        'pi': sympy.pi,
        'Rational': sympy.Rational,
        'Integer': sympy.Integer,
        'Float': sympy.Float,
        'Symbol': sympy.Symbol,
    }


def _parse_math_expr(s: str) -> Optional[sympy.Expr]:
    """Safely parse a numeric/algebraic expression (fractions, indices, quadratics,
    algebraic fractions) for symbolic/numeric comparison. Returns None if the
    string isn't a well-formed math expression."""
    s = s.strip()
    if not s or not _SAFE_MATH_RE.match(s):
        return None
    try:
        return parse_expr(
            s,
            transformations=_MATH_TRANSFORMS,
            global_dict=_safe_math_globals(),
            evaluate=True,
        )
    except Exception:
        return None


def _extract_numeric_candidates(raw_ans: str) -> List[float]:
    """Numbers to compare against a target value: prefer evaluating the whole
    answer as a math expression (so fractions like '3/4' or '2^3' resolve
    correctly), falling back to the legacy digit-scraping regex for free text."""
    expr = _parse_math_expr(raw_ans)
    if expr is not None and expr.is_number:
        try:
            return [float(expr.evalf())]
        except Exception:
            pass
    return [float(n) for n in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", raw_ans)]

# --- Word-boundary + light inflection tolerance for phrase-matching operators ---
# (CONTAIN, NOT CONTAIN, ANY, ALL, MIN). Raw substring matching used to mean
# "increase" wouldn't match "increasing", forcing every DSL author (LLM or
# admin) to enumerate every tense/plural form of a concept separately - and
# it let short terms false-positive-match inside unrelated words (CONTAIN:cat
# matching "category"). Stemming + word-boundary matching fixes both without
# changing matching semantics for anything already relying on plain substring
# behavior (this is a pure widening: anything that matched before still does).
# "es" is only a separate plural suffix after a sibilant (box/boxes,
# class/classes, wish/wishes) - anywhere else ("increase"+"s"="increases")
# it's just "s" on a word that already ends in "e", and stripping "es" as a
# unit would wrongly eat that "e" too (increases -> increas, not increase).
_STEM_ES_SUFFIXES = ("ches", "shes", "sses", "xes", "zes")

def _stem_word(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(_STEM_ES_SUFFIXES):
        return w[:-2]
    if len(w) > 4 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed") and not w.endswith("eed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w

def _normalize_phrase(s: str) -> str:
    return " ".join(_stem_word(w) for w in re.findall(r"[a-z0-9]+", s.lower()))

def _phrase_matches(term: str, answer: str) -> bool:
    norm_term = _normalize_phrase(term)
    if not norm_term:
        return False
    norm_ans = _normalize_phrase(answer)
    return re.search(r'\b' + re.escape(norm_term) + r'\b', norm_ans) is not None

def _split_top_level(s: str, operator: str) -> List[str]:
    # Deliberately case-SENSITIVE on AND/OR (unlike the rest of this module,
    # which is case-insensitive) - every DSL clause value is written in
    # lowercase, and real mark-scheme phrasing routinely contains the plain
    # English words "and"/"or" ("A, D and E", "fight or flight"). A
    # case-insensitive split would tear those apart as if they were boolean
    # connectors (confirmed against real ingested data - it does). The DSL's
    # own AND/OR keywords are always written uppercase, so matching only the
    # uppercase form disambiguates content from logic without needing any
    # escaping syntax.
    tokens = re.split(r'(\(|\)|\bAND\b|\bOR\b)', s)
    parts = []
    current = []
    depth = 0
    for t in tokens:
        t_strip = t.strip()
        if t_strip == '(':
            depth += 1
            current.append(t)
        elif t_strip == ')':
            depth -= 1
            current.append(t)
        elif depth == 0 and t_strip == operator:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(t)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]

def _is_balanced(s: str) -> bool:
    d = 0
    for c in s:
        if c == '(': d += 1
        elif c == ')':
            d -= 1
            if d < 0: return False
    return d == 0

# DSL parse tree: ('OR', [nodes]) | ('AND', [nodes]) | ('LEAF', clause_str).
# Shared by evaluate_dsl_expression and validate_dsl_syntax so both agree on
# structure (top-level OR splits first, then AND, matching standard boolean
# precedence - AND binds tighter than OR).
def _parse_dsl(expr: str):
    expr = expr.strip()
    if not expr:
        return ('LEAF', '')

    or_parts = _split_top_level(expr, "OR")
    if len(or_parts) > 1:
        return ('OR', [_parse_dsl(p) for p in or_parts])

    and_parts = _split_top_level(expr, "AND")
    if len(and_parts) > 1:
        return ('AND', [_parse_dsl(p) for p in and_parts])

    if expr.startswith("(") and expr.endswith(")") and _is_balanced(expr[1:-1]):
        return _parse_dsl(expr[1:-1])

    return ('LEAF', expr)

def _leaf_clauses(node):
    kind, payload = node
    if kind == 'LEAF':
        if payload:
            yield payload
    else:
        for child in payload:
            yield from _leaf_clauses(child)

def _mcq_token_canonical(tok: str, options_by_key: Dict[str, str], options_by_text: Dict[str, str]) -> str:
    t = tok.strip().lower()
    if t in options_by_key:
        return t
    if t in options_by_text:
        return options_by_text[t]
    return t

def _mcq_matches(val: str, raw_ans: str, answer_options: Optional[List[Dict[str, Any]]]) -> bool:
    """
    Compares an MCQ clause's value against the student's selected option(s),
    tolerant of either side being written as the option's short key (e.g.
    "A") or its full text (e.g. "cell division 1") - ingestion has, at
    different times, compiled MCQ clauses either way, and the frontend
    always submits the key(s). Without this, a DSL value written as the
    option's full text could never match the key the student actually
    submitted, silently zero-marking an objectively correct MCQ answer.
    Order-independent, so it also covers multi_select comma-lists.
    """
    options_by_key: Dict[str, str] = {}
    options_by_text: Dict[str, str] = {}
    for opt in (answer_options or []):
        if not isinstance(opt, dict):
            continue
        k = str(opt.get("key", "")).strip().lower()
        t = str(opt.get("text", "")).strip().lower()
        if k:
            options_by_key[k] = t or k
        if t:
            options_by_text[t] = k or t

    val_tokens = sorted(
        _mcq_token_canonical(tok, options_by_key, options_by_text)
        for tok in val.split(",") if tok.strip()
    )
    ans_tokens = sorted(
        _mcq_token_canonical(tok, options_by_key, options_by_text)
        for tok in raw_ans.split(",") if tok.strip()
    )
    return bool(val_tokens) and val_tokens == ans_tokens

def _mcq_partial_credit(
    val: str, raw_ans: str, answer_options: Optional[List[Dict[str, Any]]], mark_value: int
) -> Tuple[int, str, List[str]]:
    """
    Real AQA "tick N boxes" mark schemes award 1 mark per correct box ticked,
    up to the max - not all-or-nothing like _mcq_matches. Used only when the
    whole DSL is a single bare MCQ: clause (see mark_question), so it never
    changes behavior for MCQ combined with other requirements via AND/OR. For
    a single-option MCQ (1 mark) this reduces to the exact same 0/1 result
    _mcq_matches already gave, so it's a strict generalization, not a
    behavior change for ordinary single-select questions.
    """
    options_by_key: Dict[str, str] = {}
    options_by_text: Dict[str, str] = {}
    for opt in (answer_options or []):
        if not isinstance(opt, dict):
            continue
        k = str(opt.get("key", "")).strip().lower()
        t = str(opt.get("text", "")).strip().lower()
        if k:
            options_by_key[k] = t or k
        if t:
            options_by_text[t] = k or t

    val_tokens = {
        _mcq_token_canonical(tok, options_by_key, options_by_text)
        for tok in val.split(",") if tok.strip()
    }
    ans_tokens = {
        _mcq_token_canonical(tok, options_by_key, options_by_text)
        for tok in raw_ans.split(",") if tok.strip()
    }
    correct = len(val_tokens & ans_tokens)
    marks_awarded = min(correct, mark_value)
    if marks_awarded == mark_value:
        return marks_awarded, "Correct! Full marks awarded.", []
    missed = f"Correct: {correct}/{len(val_tokens)} correct option(s) selected. Expected option(s) {val}."
    return marks_awarded, missed, [missed]

_MARKS_RE = re.compile(r'^MARKS:(\d+):(.+)$', re.IGNORECASE | re.DOTALL)


def _parse_marks_clause(clause: str) -> Optional[Tuple[int, str]]:
    """
    Recognises a MARKS:<n>:<clause> wrapper - see evaluate_dsl_expression's
    docstring. Returns (n, inner_clause) or None if this clause isn't
    MARKS-wrapped. Deliberately regex-based rather than the generic
    op/val split every other operator uses (clause.split(":", 1)) since the
    inner clause is itself a normal "OP:VALUE" leaf and would otherwise be
    swallowed whole into MARKS' own "value".
    """
    m = _MARKS_RE.match(clause.strip())
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    return n, m.group(2).strip()


def evaluate_dsl_expression(
    dsl: str, student_answer: str, answer_options: Optional[List[Dict[str, Any]]] = None, mark_value: int = 1
) -> Tuple[int, str, List[str]]:
    """
    Evaluates a deterministic DSL expression with nested parentheses and AND/OR logic.
    Returns (marks_awarded, feedback_text, missed_points) - missed_points lists the
    actual clause(s) the answer failed, rather than one generic string, so a
    student gets real diagnostic detail on what was missing.

    Every clause (LEAF/AND) is worth the question's full mark_value if it's
    satisfied, UNLESS wrapped as MARKS:<n>:<clause> - a real mark scheme
    routinely states that a specific ALTERNATE answer earns fewer marks than
    full credit (e.g. "allow for 1 mark an answer of 7.2 with evidence of
    having used the wrong percentage" on a 3-mark question, alongside the
    3-mark answer of 108) - a plain OR of that alternate into the same
    all-or-nothing expression would silently award FULL marks to a wrong
    answer the mark scheme only credits partially (confirmed real case:
    AQA 8461/1H June 2025 Q01.6). MARKS:<n>:<clause> declares that if
    <clause> (an ordinary single OP:VALUE leaf, not a further nested
    AND/OR/MARKS) matches, this branch is worth n marks specifically - n is
    capped to mark_value so a compiler mistake can never award MORE than
    the question is worth. An OR of branches with different worths resolves
    to the HIGHEST-scoring branch that actually matches, matching how a real
    examiner reads alternative mark-scheme answers.
    """
    clean_ans = student_answer.strip().lower()
    raw_ans = student_answer.strip()

    def _eval_leaf_text(clause: str) -> Tuple[bool, str]:
        marks_clause = _parse_marks_clause(clause)
        inner = marks_clause[1] if marks_clause else clause
        return _eval_single_clause(inner, clean_ans, raw_ans, answer_options)

    def _eval(node) -> Tuple[int, List[str]]:
        kind, payload = node
        if kind == 'OR':
            results = [_eval(c) for c in payload]
            best_marks = max((m for m, _ in results), default=0)
            if best_marks > 0:
                return best_marks, []
            # None of the branches passed - surface the branch that came
            # closest (fewest unmet requirements) rather than every failed
            # branch's reasons, since OR branches are alternatives and
            # dumping all of them would look like the student needed to
            # satisfy every one at once.
            best = min(results, key=lambda r: len(r[1])) if results else (0, [])
            return 0, best[1]
        if kind == 'AND':
            results = [_eval(c) for c in payload]
            passed = all(m > 0 for m, _ in results)
            reasons: List[str] = []
            for m, r in results:
                if m == 0:
                    reasons.extend(r)
            return (mark_value if passed else 0), reasons
        if not payload:
            return mark_value, []
        marks_clause = _parse_marks_clause(payload)
        passed, fb = _eval_leaf_text(payload)
        awarded = min(marks_clause[0], mark_value) if marks_clause else mark_value
        return (awarded if passed else 0), ([] if passed else ([fb] if fb else ["Answer did not satisfy required mark scheme criteria."]))

    marks_awarded, reasons = _eval(_parse_dsl(dsl))
    if marks_awarded >= mark_value:
        feedback = "Correct! Full marks awarded."
    elif marks_awarded > 0:
        feedback = f"Partial credit: {marks_awarded}/{mark_value} marks awarded. " + (" ".join(reasons) if reasons else "")
    else:
        feedback = " ".join(reasons) if reasons else "Answer did not satisfy required mark scheme criteria."
    return marks_awarded, feedback.strip(), reasons

def _eval_single_clause(
    clause: str, clean_ans: str, raw_ans: str, answer_options: Optional[List[Dict[str, Any]]] = None
) -> Tuple[bool, str]:
    if ":" not in clause:
        return clean_ans == clause.lower(), f"Expected '{clause}'"

    op, val = clause.split(":", 1)
    op = op.strip().upper()
    val = val.strip()

    if op == "MCQ":
        passed = _mcq_matches(val, raw_ans, answer_options)
        return passed, f"Expected option {val}" if not passed else ""

    elif op == "CONTAIN":
        passed = _phrase_matches(val, clean_ans)
        return passed, f"Must include '{val}'" if not passed else ""

    elif op == "NOT CONTAIN":
        passed = not _phrase_matches(val, clean_ans)
        return passed, f"Must not include '{val}'" if not passed else ""

    elif op == "ANY":
        options = [o.strip() for o in val.split(",")]
        passed = any(_phrase_matches(opt, clean_ans) for opt in options)
        return passed, f"Must mention one of: {val}" if not passed else ""

    elif op == "ALL":
        terms = [t.strip() for t in val.split(",")]
        missing = [t for t in terms if not _phrase_matches(t, clean_ans)]
        passed = len(missing) == 0
        return passed, f"Missing required terms: {', '.join(missing)}" if not passed else ""

    elif op == "MIN":
        # "at least N of these M points" - the common "any two from: ..."
        # mark-scheme pattern, which ANY (passes on just one) can't express.
        n_str, _, terms_str = val.partition(":")
        try:
            n = int(n_str.strip())
        except ValueError:
            return False, f"Invalid MIN count '{n_str}'"
        terms = [t.strip() for t in terms_str.split(",") if t.strip()]
        matched = [t for t in terms if _phrase_matches(t, clean_ans)]
        passed = len(matched) >= n
        return passed, f"Need at least {n} of: {terms_str} (found {len(matched)})" if not passed else ""

    elif op == "EXACT":
        # Symbolic/numeric equivalence first: handles fractions ("3/4"), indices
        # ("2^3"), and algebraic expressions like quadratics or algebraic
        # fractions ("(x+2)/(x-3)") regardless of how they're arranged/simplified.
        target_expr = _parse_math_expr(val)
        given_expr = _parse_math_expr(raw_ans)
        if target_expr is not None and given_expr is not None:
            try:
                diff = sympy.simplify(sympy.expand(target_expr - given_expr))
                if diff == 0 or (diff.is_number and abs(complex(diff.evalf())) < 1e-6):
                    return True, ""
            except Exception:
                pass
        try:
            val_num = float(val)
            nums = _extract_numeric_candidates(raw_ans)
            passed = any(abs(n - val_num) < 1e-5 for n in nums)
            return passed, f"Expected exact value {val}" if not passed else ""
        except Exception:
            passed = clean_ans == val.lower()
            return passed, f"Expected '{val}'" if not passed else ""

    elif op == "RANGE":
        try:
            parts = [float(p.strip()) for p in val.split(",")]
            nums = _extract_numeric_candidates(raw_ans)
            passed = any(parts[0] <= n <= parts[1] for n in nums)
            return passed, f"Value must be between {parts[0]} and {parts[1]}" if not passed else ""
        except Exception:
            return False, f"Invalid numerical value in range {val}"
            
    elif op == "REGEX":
        try:
            passed = bool(re.search(val, raw_ans, re.IGNORECASE))
            return passed, f"Answer must match pattern {val}" if not passed else ""
        except Exception:
            return False, "Regex evaluation error"
            
    return False, f"Unknown DSL operator: {op}"

_KNOWN_DSL_OPERATORS = {"MCQ", "CONTAIN", "NOT CONTAIN", "ANY", "ALL", "MIN", "EXACT", "RANGE", "REGEX"}
# Longest-name-first so "NOT CONTAIN:" isn't missed in favor of a partial "CONTAIN:" match.
_EMBEDDED_OP_RE = re.compile(
    r'\b(?:NOT CONTAIN|CONTAIN|ANY|ALL|MIN|EXACT|RANGE|MCQ|REGEX|MARKS)\s*:', re.IGNORECASE
)

def _is_valid_math_value(v: str) -> bool:
    """EXACT/RANGE values should be a plain number or an algebraic expression
    using short single-letter unknowns (x, y, t, ...) - the UK exam-answer
    convention. Multi-letter symbols (g, m2, year, ...) mean a unit or word
    got left inside the value instead of being a pure numeric/algebraic
    expression, which silently degrades EXACT to a near-useless string
    equality check at grading time (see EXACT:34 g/m2/year in real ingested
    data)."""
    expr = _parse_math_expr(v)
    if expr is None:
        return False
    if expr.is_number:
        return True
    free_syms = expr.free_symbols
    return bool(free_syms) and all(len(str(s)) == 1 for s in free_syms)

def _is_pure_not_contain(node) -> bool:
    """True if this branch of the tree offers no positive requirement at
    all - just NOT CONTAIN clause(s), possibly ANDed together. OR-ing a
    branch like this into an expression makes the whole OR pass for nearly
    any answer, since NOT CONTAIN of a specific phrase is true for almost
    all input (see the real 'ANY:... OR NOT CONTAIN:...' bug)."""
    kind, payload = node
    if kind == 'LEAF':
        marks_clause = _parse_marks_clause(payload)
        text = marks_clause[1] if marks_clause else payload
        return text.strip().upper().startswith("NOT CONTAIN")
    if kind == 'AND':
        return all(_is_pure_not_contain(c) for c in payload)
    return False

def _validate_leaf_text(clause: str, problems: List[str]) -> None:
    """Validates one ordinary OP:VALUE leaf clause (never a MARKS wrapper
    itself - see the MARKS branch in validate_dsl_syntax, which peels that
    off and calls back in with just the inner clause), appending any
    problems found to `problems` in place."""
    if ":" not in clause:
        problems.append(f"Clause has no operator: '{clause}'")
        return

    op, val = clause.split(":", 1)
    op = op.strip().upper()
    val = val.strip()

    if op not in _KNOWN_DSL_OPERATORS:
        problems.append(f"Unknown operator '{op}' in clause: '{clause}'")
        return

    if _EMBEDDED_OP_RE.search(val):
        problems.append(
            f"Operator keyword found inside another clause's value (missing AND/OR "
            f"between clauses, or an operator nested inside ANY/ALL/MIN): '{clause}'"
        )

    if op == "MIN":
        n_str, _, terms_str = val.partition(":")
        terms = [t.strip() for t in terms_str.split(",") if t.strip()]
        try:
            n = int(n_str.strip())
            if n < 1 or n > len(terms):
                problems.append(f"MIN count {n} is out of range for {len(terms)} term(s): '{clause}'")
        except ValueError:
            problems.append(f"MIN clause missing a valid integer count: '{clause}'")
    elif op == "EXACT":
        if not _is_valid_math_value(val):
            problems.append(f"EXACT value isn't a valid numeric/algebraic expression: '{clause}'")
    elif op == "RANGE":
        parts = [p.strip() for p in val.split(",")]
        if len(parts) != 2 or not all(_is_valid_math_value(p) for p in parts):
            problems.append(f"RANGE value isn't a valid 'min,max' pair: '{clause}'")
    elif op == "REGEX":
        try:
            re.compile(val)
        except re.error as e:
            problems.append(f"Invalid regex pattern '{val}': {e}")

def validate_dsl_syntax(dsl: str) -> List[str]:
    """
    Lints a marking_dsl string for structural/logic problems without needing
    a student answer to test it against. Returns a list of human-readable
    problems (empty if well-formed). Intended to run at ingestion time so a
    broken DSL is caught before it silently mis-marks students in production.
    """
    problems: List[str] = []
    if not dsl or not dsl.strip():
        return ["DSL is empty"]

    if not _is_balanced(dsl):
        problems.append("Unbalanced parentheses")

    tree = _parse_dsl(dsl)

    for clause in _leaf_clauses(tree):
        marks_clause = _parse_marks_clause(clause)
        if marks_clause is not None:
            n, inner = marks_clause
            if n < 1:
                problems.append(f"MARKS count must be a positive integer: '{clause}'")
            # A MARKS-wrapped inner clause is validated the same as any
            # ordinary leaf - it just isn't ALSO run through the generic
            # embedded-operator check above it (that check exists to catch
            # a stray operator keyword left inside a plain leaf's value by
            # mistake, which is exactly what MARKS:n:OP:VALUE legitimately
            # looks like by design).
            _validate_leaf_text(inner, problems)
            continue
        _validate_leaf_text(clause, problems)

    def _check_or_branches(node):
        kind, payload = node
        if kind == 'OR':
            for branch in payload:
                if _is_pure_not_contain(branch):
                    problems.append(
                        "An OR branch consists only of NOT CONTAIN clause(s) with no positive "
                        "requirement - this makes the whole OR pass for nearly any answer"
                    )
                _check_or_branches(branch)
        elif kind == 'AND':
            for child in payload:
                _check_or_branches(child)

    _check_or_branches(tree)
    return problems

_JSON_FENCE_RE = re.compile(r'^```(?:json)?\s*\n?(.*?)\n?```\s*$', re.DOTALL)

def _find_json_value_end(text: str, start: int) -> Optional[int]:
    """
    Given text[start] == '{' or '[', returns the index just past the
    matching closing bracket by counting depth while respecting quoted
    strings/escapes (so a brace/bracket character inside a string value
    doesn't throw off the count). Returns None if the value is never closed
    (e.g. generation was cut off mid-object by a repetition loop hitting
    max_tokens - see the "Extra data"/never-closes cases below).
    """
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _extract_marking_json(raw: str) -> Dict[str, Any]:
    """
    Robustly extracts the marking JSON object from the self-hosted model's
    raw text response. Despite the system prompt's "output ONLY JSON"
    instruction, the model isn't perfectly disciplined about it - real,
    observed deviations (from live production traffic, not just eval) that
    a bare json.loads(raw) chokes on and silently diverts to the crude
    keyword-overlap fallback scorer instead of the actual grading:
    1. Wrapping the JSON in a markdown code fence (```json ... ```) -
       json.loads fails outright on the fence characters.
    2. Returning a JSON array of multiple candidate objects (not just the
       single-element-array case the caller already unwraps) rather than
       one bare object.
    3. Trailing content after a complete JSON object - most often the model
       degenerating into a repetition loop (see evaluate.py's
       failures_sample) and continuing to emit text past the object that
       already answered the prompt. json.loads raises "Extra data" on the
       whole string even though the object itself is perfectly valid -
       isolate just the first balanced {...}/[...] by bracket-counting
       (respecting quoted strings) instead of assuming the entire response
       is exactly one JSON value.
    4. A literal (unescaped) control character - e.g. a raw newline inside
       a bullet string - which json.loads' default strict=True rejects
       outright even though the surrounding structure is fine; parse with
       strict=False instead, which permits control characters in strings.
    Raises (same as a bare json.loads/dict access would) if nothing
    recoverable is found - the caller's except block already handles that.
    """
    text = raw.strip()
    fence_match = _JSON_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()

    start = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if start is not None:
        end = _find_json_value_end(text, start)
        if end is not None:
            text = text[start:end]

    data = json.loads(text, strict=False)
    if isinstance(data, list):
        dict_items = [d for d in data if isinstance(d, dict)]
        if not dict_items:
            raise ValueError("No JSON object found in array response")
        data = dict_items[0]
    return data

_LEADING_BULLET_RE = re.compile(r'^[\s]*[-•*][\s]+')

def _strip_leading_bullet(s: str) -> str:
    """Strips a leading '- '/'• '/'* ' marker some feedback bullets carry
    over verbatim from raw mark-scheme text, so the frontend's own ✓/✗ icon
    isn't followed by a redundant literal dash."""
    return _LEADING_BULLET_RE.sub('', s, count=1)

async def mark_question(
    question_text: str,
    mark_value: int,
    marking_type: str,
    marking_dsl: str | None,
    mark_scheme_text: str | None,
    student_answer: str,
    command_word: str | None = None,
    spec_code: str | None = None,
    ai_marking_status: str = "none",
    exam_board: str | None = None,
    level: str | None = None,
    subject: str | None = None,
    tier: str | None = None,
    answer_options: Any = None,
    student_first_name: str | None = None,
    known_misconceptions: List[Dict[str, str]] | None = None,
    table_data: Any = None
) -> Dict[str, Any]:
    """
    Evaluates student answer:
    - numeric/select/multi_select/grid_select questions: evaluated
      deterministically via DSL in <10ms, since these have one structurally
      fixed correct value/option regardless of mark value.
    - Every other (free-text "written") question: evaluated via the
      self-hosted, per-spec-code model (§6.5) against the official mark
      scheme rubric, but only once that spec code's model has cleared
      rollout gating (§6.3/§6.4) - callers pass `ai_marking_status` from
      spec_code_marking_models.status; any value other than 'live' falls
      back to `needs_review`, matching §6.4 ("marking blocked ... for any
      spec code without a gated model"). Keyword/phrase-matching DSL
      operators (CONTAIN/ANY/ALL/MIN) are no longer used for written answers
      - they proved too brittle on short reasoning answers, zeroing
      genuinely correct but differently-worded science.
    """
    # 0. Practical (draw/complete/label on paper) - nothing typed to grade;
    # the frontend never collects or submits an answer for these, but guard
    # here too rather than falling through to the AI path on any stray call.
    if marking_type == "practical":
        return {
            "marks_awarded": 0,
            "marks_possible": mark_value,
            "marked_by": "practical",
            "feedback_text": "This question is self-checked against the mark scheme, not automatically marked.",
            "www": [],
            "missed_points": [],
            "misconception_tags": []
        }

    # 1. Deterministic DSL Path (numeric/select/multi_select/grid_select only)
    if marking_type == "dsl" and marking_dsl:
        parsed_options = answer_options
        if isinstance(parsed_options, str):
            try:
                parsed_options = json.loads(parsed_options)
            except Exception:
                parsed_options = None
        parsed_options = parsed_options if isinstance(parsed_options, list) else None

        # A bare "MCQ:..." clause (not combined with other requirements via
        # AND/OR) gets partial credit - 1 mark per correct box ticked, up to
        # the max, matching real AQA "tick N boxes" mark schemes. Compound
        # MCQ clauses keep the exact-match boolean evaluator below.
        dsl_root = _parse_dsl(marking_dsl)
        if dsl_root[0] == 'LEAF' and dsl_root[1].strip().upper().startswith('MCQ:'):
            _, mcq_val = dsl_root[1].split(":", 1)
            marks_awarded, fb, missed = _mcq_partial_credit(mcq_val.strip(), student_answer, parsed_options, mark_value)
            return {
                "marks_awarded": marks_awarded,
                "marks_possible": mark_value,
                "marked_by": "dsl",
                "feedback_text": fb,
                "www": [],
                "missed_points": missed,
                "misconception_tags": []
            }

        marks_awarded, fb, missed = evaluate_dsl_expression(
            marking_dsl, student_answer, parsed_options, mark_value
        )
        return {
            "marks_awarded": marks_awarded,
            "marks_possible": mark_value,
            "marked_by": "dsl",
            "feedback_text": fb,
            "www": [],
            "missed_points": missed,
            "misconception_tags": []
        }
    
    # 2. AI Spec Model Path (3+ Marks) - blocked until this spec code's
    # self-hosted model is live (§6.3/§6.4). No AI mark is shown; the
    # answer is queued for manual review instead of guessing with an
    # ungated/nonexistent model.
    if ai_marking_status != "live":
        return {
            "marks_awarded": None,
            "marks_possible": mark_value,
            "marked_by": "pending_model",
            "feedback_text": "AI marking for this specification isn't available yet. An examiner will need to review this answer.",
            "www": [],
            "missed_points": [],
            "misconception_tags": []
        }

    # Misconception tags are constrained to the approved taxonomy passed in
    # (mirrors ai_pipeline.py::blind_grade_synthetic_answer's known_misconceptions
    # pattern) - an unconstrained free-text tag can't be matched reliably
    # across attempts or shown to a student with a real label, so the model
    # may only *attach* one of these known tag_ids, never invent its own
    # phrasing for misconception_tags. A genuine misconception with no
    # matching approved tag can still be surfaced via new_tag_suggestion,
    # which the caller queues into misconception_taxonomy as pending
    # (approved_at NULL) rather than ever writing straight to a student's
    # profile.
    known_ids = {t["tag_id"] for t in known_misconceptions} if known_misconceptions else set()
    tags_list = (
        "\n".join(f"- {t['tag_id']}: {t['label']}" for t in known_misconceptions)
        if known_misconceptions else "(none approved yet for this specification)"
    )

    # §marking_prompt: the single source of truth shared with training/
    # assemble_dataset.py's fine-tuning corpus and ai_pipeline.py's
    # synthetic-answer grader - training and serving must never diverge.
    system_prompt = marking_prompt.build_system_prompt(student_first_name)

    # table_data (raw pdfplumber rows) is stored separately from
    # question_text (see ingestion.py) so the student-facing question never
    # carries a raw pipe-separated dump of it - but the model still needs
    # the actual values to mark a question that asks about them, so it's
    # rendered and appended here at prompt-build time instead.
    parsed_tables = table_data
    if isinstance(parsed_tables, str):
        try:
            parsed_tables = json.loads(parsed_tables)
        except (TypeError, ValueError):
            parsed_tables = None
    table_block = f"\n\n    Table data referenced by this question:\n    \"\"\"{table_utils.render_tables_as_text(parsed_tables)}\"\"\"" if parsed_tables else ""

    user_prompt = f"""
    Question: {question_text}{table_block}
    Total Marks: {mark_value}
    Specification Code: {spec_code or 'General Science'}
    Command Word: {command_word or 'Explain'}
    Student's first name: {student_first_name or '(unknown - address them as "you")'}
    Official Mark Scheme:
    \"\"\"{mark_scheme_text or 'Award marks for accurate scientific reasoning.'}\"\"\"

    Student Answer:
    \"\"\"{student_answer}\"\"\"

    Approved misconception tags for this specification (choose from these only for misconception_tags):
    {tags_list}
    """

    # NEVER call a hosted API here - see ai_pipeline.mark_with_selfhosted_model.
    slug = spec_code_slug(exam_board, level, subject, tier) if exam_board and level and subject else None
    raw_ai_res = await ai_pipeline.mark_with_selfhosted_model(user_prompt, system_prompt=system_prompt, spec_slug=slug)

    try:
        data = _extract_marking_json(raw_ai_res)
        marks_awarded = min(max(int(data.get("marks_awarded", 0)), 0), mark_value)
        www = [_strip_leading_bullet(w) for w in data.get("www", []) if isinstance(w, str) and w.strip()]
        ebi = [_strip_leading_bullet(e) for e in data.get("ebi", []) if isinstance(e, str) and e.strip()]
        # Deterministic guardrail for the two-rule ebi/www consistency
        # requirement stated in the prompt above: "full marks -> ebi must be
        # empty" and "zero marks -> www must be empty" are both 100%
        # mechanically checkable from marks_awarded alone, so this doesn't
        # rely on the model actually following the instruction (confirmed
        # via live testing that even a fine-tuned adapter violates it on
        # "any N from" list-style mark schemes often enough to matter) -
        # it force-corrects the one case that instruction can never excuse:
        # a list claiming credit/deficiency that marks_awarded already
        # contradicts. Mirrors the fail-closed pattern already used below
        # for misconception tags.
        if marks_awarded == mark_value:
            ebi = []
        if marks_awarded == 0:
            www = []
        # Fail closed like the DSL evaluator does: a tag_id the model invented
        # instead of picking from the approved list is dropped rather than
        # trusted, since an unapproved/unmatched tag_id has no label/description
        # to show a student and would silently corrupt student_misconceptions
        # (this is the exact bug that let raw strings like "None" and "No
        # significant misconceptions identified." end up stored as if they
        # were real tags).
        raw_tags = data.get("misconception_tags", [])
        misconception_tags = [
            t for t in raw_tags if isinstance(t, str) and t in known_ids
        ] if isinstance(raw_tags, list) else []

        new_tag_suggestion = data.get("new_tag_suggestion")
        if not (
            isinstance(new_tag_suggestion, dict)
            and all(isinstance(new_tag_suggestion.get(k), str) and new_tag_suggestion.get(k).strip()
                    for k in ("tag_id", "label", "description"))
            and new_tag_suggestion["tag_id"] not in known_ids
        ):
            new_tag_suggestion = None

        # feedback_text/missed_points stay the stored/displayed shape (no
        # schema change downstream) - built directly from the model's own
        # www/ebi bullets, trusted as-is rather than run through any
        # deterministic post-hoc filtering of what it decided to include.
        feedback_sections = []
        if www:
            feedback_sections.append("**What went well:**\n" + "\n".join(f"- {w}" for w in www))
        if ebi:
            feedback_sections.append("**Even better if:**\n" + "\n".join(f"- {e}" for e in ebi))
        feedback_text = "\n\n".join(feedback_sections) or f"Awarded {marks_awarded}/{mark_value} marks."

        return {
            "marks_awarded": marks_awarded,
            "marks_possible": mark_value,
            "marked_by": "ai",
            "feedback_text": feedback_text,
            "www": www,
            "missed_points": ebi,
            "misconception_tags": misconception_tags,
            "new_tag_suggestion": new_tag_suggestion
        }
    except Exception as e:
        print(f"Error parsing AI marking output: {e}, falling back to rubric parser")
        # Rule-based fallback if parsing fails
        clean_ans = student_answer.lower().strip()
        score = 0
        matched = []
        missed = []
        misconceptions = []

        if mark_scheme_text:
            points = [p.strip() for p in mark_scheme_text.split("\n") if p.strip()]
            for p in points:
                keywords = [w for w in re.findall(r'\b[a-zA-Z]{4,}\b', p.lower()) if w not in {'which', 'where', 'their', 'there', 'because', 'allow', 'accept', 'marks'}]
                if keywords and any(kw in clean_ans for kw in keywords[:3]):
                    score += 1
                    matched.append(p)
                else:
                    missed.append(p)
            score = min(max(score, 0), mark_value)
        else:
            score = min(mark_value, max(1, len(clean_ans.split()) // 10))
            
        if "mitosis" in clean_ans and "gamete" in clean_ans:
            misconceptions.append("confuses_mitosis_meiosis")
        if "respiration" in clean_ans and "breathing" in clean_ans:
            misconceptions.append("confuses_respiration_breathing")
        if "artery" in clean_ans and "valve" in clean_ans:
            misconceptions.append("confuses_artery_vein_structure")
            
        name_prefix = f"{student_first_name}, y" if student_first_name else "Y"
        return {
            "marks_awarded": score,
            "marks_possible": mark_value,
            "marked_by": "ai",
            "feedback_text": f"{name_prefix}ou were awarded {score}/{mark_value} marks based on mark scheme criteria.",
            "www": [_strip_leading_bullet(m) for m in matched[:3]],
            "missed_points": [_strip_leading_bullet(m) for m in missed[:3]],
            "misconception_tags": [m for m in misconceptions if m in known_ids],
            "new_tag_suggestion": None
        }
