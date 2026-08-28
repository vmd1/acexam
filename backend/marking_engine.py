import re
import json
from typing import Dict, Any, List, Tuple
import ai_pipeline

def evaluate_dsl_expression(dsl: str, student_answer: str) -> Tuple[bool, str]:
    """
    Evaluates a deterministic DSL expression with nested parentheses and AND/OR logic.
    """
    clean_ans = student_answer.strip().lower()
    raw_ans = student_answer.strip()
    
    def eval_expr(expr: str) -> bool:
        expr = expr.strip()
        if not expr:
            return True
            
        # Check for top-level OR
        or_split = _split_top_level(expr, "OR")
        if len(or_split) > 1:
            return any(eval_expr(part) for part in or_split)
            
        # Check for top-level AND
        and_split = _split_top_level(expr, "AND")
        if len(and_split) > 1:
            return all(eval_expr(part) for part in and_split)
            
        # Strip surrounding parentheses
        if expr.startswith("(") and expr.endswith(")"):
            if _is_balanced(expr[1:-1]):
                return eval_expr(expr[1:-1])
                
        passed, _ = _eval_single_clause(expr.strip("()"), clean_ans, raw_ans)
        return passed

    def _split_top_level(s: str, operator: str) -> List[str]:
        parts = []
        current = []
        depth = 0
        tokens = re.split(r'(\(|\)|\bAND\b|\bOR\b)', s, flags=re.IGNORECASE)
        for t in tokens:
            t_strip = t.strip()
            if t_strip == '(':
                depth += 1
                current.append(t)
            elif t_strip == ')':
                depth -= 1
                current.append(t)
            elif depth == 0 and t_strip.upper() == operator:
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

    passed = eval_expr(dsl)
    feedback = "Correct! Full marks awarded." if passed else "Answer did not satisfy required mark scheme criteria."
    return passed, feedback

def _eval_single_clause(clause: str, clean_ans: str, raw_ans: str) -> Tuple[bool, str]:
    if ":" not in clause:
        return clean_ans == clause.lower(), f"Expected '{clause}'"
        
    op, val = clause.split(":", 1)
    op = op.strip().upper()
    val = val.strip()
    
    if op == "MCQ":
        return clean_ans == val.lower(), f"Expected option {val}"
        
    elif op == "CONTAIN":
        passed = val.lower() in clean_ans
        return passed, f"Must include '{val}'" if not passed else ""
        
    elif op == "NOT CONTAIN":
        passed = val.lower() not in clean_ans
        return passed, f"Must not include '{val}'" if not passed else ""
        
    elif op == "ANY":
        options = [o.strip().lower() for o in val.split(",")]
        passed = any(opt in clean_ans for opt in options)
        return passed, f"Must mention one of: {val}" if not passed else ""
        
    elif op == "ALL":
        terms = [t.strip().lower() for t in val.split(",")]
        missing = [t for t in terms if t not in clean_ans]
        passed = len(missing) == 0
        return passed, f"Missing required terms: {', '.join(missing)}" if not passed else ""
        
    elif op == "EXACT":
        try:
            val_num = float(val)
            nums = [float(n) for n in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", raw_ans)]
            passed = any(abs(n - val_num) < 1e-5 for n in nums)
            return passed, f"Expected exact value {val}" if not passed else ""
        except Exception:
            passed = clean_ans == val.lower()
            return passed, f"Expected '{val}'" if not passed else ""
            
    elif op == "RANGE":
        try:
            parts = [float(p.strip()) for p in val.split(",")]
            nums = [float(n) for n in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", raw_ans)]
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

async def mark_question(
    question_text: str,
    mark_value: int,
    marking_type: str,
    marking_dsl: str | None,
    mark_scheme_text: str | None,
    student_answer: str,
    command_word: str | None = None,
    spec_code: str | None = None
) -> Dict[str, Any]:
    """
    Evaluates student answer:
    - 1-2 mark questions: Evaluated deterministically via DSL in <10ms.
    - 3+ mark questions: Evaluated via LLM / Spec-Code Model against official mark scheme rubric.
    """
    # 1. Deterministic DSL Path (1-2 Marks)
    if marking_type == "dsl" and marking_dsl:
        passed, fb = evaluate_dsl_expression(marking_dsl, student_answer)
        marks_awarded = mark_value if passed else 0
        missed = [] if passed else [fb]
        return {
            "marks_awarded": marks_awarded,
            "marks_possible": mark_value,
            "marked_by": "dsl",
            "feedback_text": "Correct! Full marks awarded." if passed else fb,
            "missed_points": missed,
            "misconception_tags": []
        }
    
    # 2. AI Spec Model Path (3+ Marks)
    system_prompt = (
        "You are an official UK GCSE/A-Level exam board marker (AQA/Edexcel/OCR). "
        "Mark the student's answer strictly and objectively against the provided mark scheme. "
        "Award integer marks between 0 and maximum possible marks. "
        "Identify specific missed marking points and any known misconception tags. "
        "Output strictly valid JSON with keys: 'marks_awarded' (int), 'feedback_text' (str), 'missed_points' (list of str), 'misconception_tags' (list of str)."
    )
    
    user_prompt = f"""
    Question: {question_text}
    Total Marks: {mark_value}
    Specification Code: {spec_code or 'General Science'}
    Command Word: {command_word or 'Explain'}
    Official Mark Scheme:
    \"\"\"{mark_scheme_text or 'Award marks for accurate scientific reasoning.'}\"\"\"

    Student Answer:
    \"\"\"{student_answer}\"\"\"
    """
    
    # NEVER call a hosted API here - see ai_pipeline.mark_with_selfhosted_model.
    raw_ai_res = await ai_pipeline.mark_with_selfhosted_model(user_prompt, system_prompt=system_prompt)
    
    try:
        data = json.loads(raw_ai_res)
        marks_awarded = min(max(int(data.get("marks_awarded", 0)), 0), mark_value)
        feedback_text = data.get("feedback_text", f"Awarded {marks_awarded}/{mark_value} marks.")
        missed_points = data.get("missed_points", [])
        misconception_tags = data.get("misconception_tags", [])
        
        return {
            "marks_awarded": marks_awarded,
            "marks_possible": mark_value,
            "marked_by": "ai",
            "feedback_text": feedback_text,
            "missed_points": missed_points,
            "misconception_tags": misconception_tags
        }
    except Exception as e:
        print(f"Error parsing AI marking output: {e}, falling back to rubric parser")
        # Rule-based fallback if parsing fails
        clean_ans = student_answer.lower().strip()
        score = 0
        missed = []
        misconceptions = []
        
        if mark_scheme_text:
            points = [p.strip() for p in mark_scheme_text.split("\n") if p.strip()]
            for p in points:
                keywords = [w for w in re.findall(r'\b[a-zA-Z]{4,}\b', p.lower()) if w not in {'which', 'where', 'their', 'there', 'because', 'allow', 'accept', 'marks'}]
                if keywords and any(kw in clean_ans for kw in keywords[:3]):
                    score += 1
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
            
        return {
            "marks_awarded": score,
            "marks_possible": mark_value,
            "marked_by": "ai",
            "feedback_text": f"Awarded {score}/{mark_value} marks based on mark scheme criteria.",
            "missed_points": missed[:3],
            "misconception_tags": misconceptions
        }
