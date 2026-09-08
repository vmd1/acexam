"""
§6.3 stage 2 / §6.5 held-out evaluation harness: runs a fine-tuned adapter
against test.jsonl (the series held out entirely from training by
assemble_dataset.py) and scores it two ways, since tagging is more
subjective than mark counting and shouldn't be exact-string-matched:

- exact-match rate on marks_awarded (the number that actually decides a
  student's score - this is the number that must be right)
- keyword-overlap score on www / ebi / misconception_tags (looser, since
  two examiners phrase the same feedback point differently)

Only checkpoints clearing the §6.3 stage-2 threshold become launch
candidates for live marking - this script reports the numbers a human
uses to make that call, it doesn't auto-gate anything itself.

Generation here is one example at a time, no batching (mlx_lm.generate has
no built-in batch API) - fine for a handful of examples, but a held-out set
of 100+ (a spec code held out an entire large paper series in full,
§6.5) turns into several minutes of wall-clock time regardless of how idle
the machine is. Since agent.py runs this automatically after every training
job (manual or auto-triggered, §6.5), eval defaults to a random sample of
DEFAULT_EVAL_SAMPLE_SIZE rather than the full held-out set - still a solid
estimate of the real exact-match rate, an order of magnitude faster. Pass
--full for the exhaustive run, or --limit to pick a different sample size.
"""
import argparse
import json
import random
import re
import sys
from pathlib import Path

from mlx_lm import generate, load

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from marking_prompt import build_system_prompt, prompt_hash  # noqa: E402

DATA_ROOT = Path(__file__).parent / "data"
ADAPTERS_ROOT = Path(__file__).parent / "adapters"

DEFAULT_MODEL = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
DEFAULT_EVAL_SAMPLE_SIZE = 50
EVAL_SAMPLE_SEED = 42  # mirrors assemble_dataset.py's SEED, for reproducible sampling run-to-run

# Mark-scheme shapes to report separately, not just as one aggregate number -
# an aggregate average hid a systematic ~75% failure rate on "any N from"
# list-style schemes this session (see SLICE_PATTERNS below) that a random
# 50-sample average made look like ordinary noise. Matches the DSL's own
# MIN:n:... operator naming for the same mark-scheme pattern
# (marking_engine.py::_eval_single_clause).
SLICE_PATTERNS = {
    "any_n_from_list": re.compile(r"\bany\s+(?:one|two|three|four|five|six|\d+)\s+(?:from|of)\b", re.IGNORECASE),
    "levels_of_response": re.compile(r"\blevel\s*[123]\b|\bindicative content\b", re.IGNORECASE),
}


def classify_slices(mark_scheme_text):
    """Returns the list of slice names (SLICE_PATTERNS keys) this example's
    mark scheme matches - an example can match more than one, or none
    ("other")."""
    text = mark_scheme_text or ""
    matched = [name for name, pattern in SLICE_PATTERNS.items() if pattern.search(text)]
    return matched or ["other"]


def normalize_tokens(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def overlap_score(predicted_list, gold_list):
    """Token-overlap F1 over the union of predicted vs. gold string lists."""
    if not gold_list and not predicted_list:
        return 1.0
    if not gold_list or not predicted_list:
        return 0.0
    pred_tokens = set()
    for item in predicted_list:
        pred_tokens |= normalize_tokens(str(item))
    gold_tokens = set()
    for item in gold_list:
        gold_tokens |= normalize_tokens(str(item))
    if not pred_tokens or not gold_tokens:
        return 0.0
    overlap = len(pred_tokens & gold_tokens)
    precision = overlap / len(pred_tokens)
    recall = overlap / len(gold_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


_TOTAL_MARKS_RE = re.compile(r"Total Marks:\s*(\d+)")


def _mark_value_from_prompt(user_content):
    """Recovers the question's max marks from the user prompt (see
    assemble_dataset.py::to_chat_example's "Total Marks: {mark_value}"
    line) so the contradiction check below can tell "full marks" from
    "partial"."""
    m = _TOTAL_MARKS_RE.search(user_content or "")
    return int(m.group(1)) if m else None


def parse_completion(raw):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Model may wrap JSON in text/markdown fences despite the schema
        # instruction - salvage the first {...} block before giving up.
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("spec_slug")
    parser.add_argument("--adapter-path", default=None,
                         help="path to a trained adapter dir, e.g. training/adapters/<slug>/v-<version>. "
                              "Omit to evaluate the bare base model zero-shot (e.g. comparing candidate "
                              "base models before committing to fine-tune one).")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=DEFAULT_EVAL_SAMPLE_SIZE,
                         help=f"random sample size to evaluate (default {DEFAULT_EVAL_SAMPLE_SIZE}); ignored with --full")
    parser.add_argument("--full", action="store_true", help="evaluate the entire held-out set instead of a sample")
    parser.add_argument("--out", default=None,
                         help="where to write eval_result.json - defaults to <adapter-path>/eval_result.json "
                              "if --adapter-path is given, otherwise required")
    args = parser.parse_args()
    if args.adapter_path is None and args.out is None:
        raise SystemExit("--out is required when --adapter-path is omitted (no adapter dir to default into)")

    test_path = DATA_ROOT / args.spec_slug / "test.jsonl"
    with open(test_path) as f:
        examples = [json.loads(line) for line in f]
    held_out_total = len(examples)
    sampled = not args.full and args.limit and args.limit < len(examples)
    if sampled:
        examples = random.Random(EVAL_SAMPLE_SEED).sample(examples, args.limit)

    if args.adapter_path:
        print(f"Loading {args.model} with adapter {args.adapter_path} ...")
        model, tokenizer = load(args.model, adapter_path=args.adapter_path)
    else:
        print(f"Loading {args.model} zero-shot (no adapter) ...")
        model, tokenizer = load(args.model)

    # Always evaluate against the CURRENT marking_prompt.py system prompt,
    # not whatever was baked into this test.jsonl's stored system message at
    # assembly time. When the corpus was just (re)built from the current
    # prompt (the normal post-train path), these are identical - but this is
    # also what makes it free to answer "does a prompt change I'm
    # considering hold up against the adapter I already have?" without a
    # retrain: edit marking_prompt.py, rerun evaluate.py against an existing
    # --adapter-path, and see the effect before spending 2-3 hours retraining
    # to find out the hard way (this session's actual mistake).
    current_prompt = build_system_prompt(None)
    current_hash = prompt_hash()
    print(f"Evaluating against current marking_prompt.py (hash {current_hash})")

    n = 0
    n_exact_mark = 0
    n_parsed = 0
    overlap_www = []
    overlap_ebi = []
    overlap_misconceptions = []
    # Tracks the exact self-contradiction pattern found in live marking:
    # marks_awarded at the maximum but ebi still non-empty ("full marks,
    # but here's something you're still missing" can't both be true) - the
    # metric this whole model-comparison run exists to measure, since it's
    # an instruction-following failure (ignoring a stated hard rule), not a
    # knowledge gap, and IFEval-style benchmarks don't test it directly.
    n_contradictory = 0
    # Mirror image, found later in live testing on Llama: marks_awarded at
    # 0 but www still non-empty ("nothing earned, but here's something you
    # got right" is the same self-contradiction the other direction). Both
    # directions are stated as one hard rule in the live system prompt
    # (marking_engine.py) - a model can satisfy one half and violate the
    # other, so both need their own counter.
    n_contradictory_zero_marks_with_www = 0
    failures = []

    # Per-mark-scheme-shape breakdown (see SLICE_PATTERNS) - an aggregate
    # average across all shapes hid a systematic failure on one shape this
    # session; slicing surfaces that automatically instead of relying on
    # someone noticing it by hand during live testing.
    slices = {name: {"n": 0, "n_exact_mark": 0, "n_contradictory": 0, "n_contradictory_zero": 0} for name in list(SLICE_PATTERNS) + ["other"]}

    for ex in examples:
        messages = ex["messages"]
        _stored_system_msg, user_msg, assistant_msg = messages[0], messages[1], messages[2]
        gold = json.loads(assistant_msg["content"])

        prompt = tokenizer.apply_chat_template(
            [{"role": "system", "content": current_prompt}, user_msg], add_generation_prompt=True, tokenize=False
        )
        raw = generate(model, tokenizer, prompt=prompt, max_tokens=512, verbose=False)
        predicted = parse_completion(raw)

        scheme_match = re.search(r'Official Mark Scheme:\s*"""(.*?)"""', user_msg["content"], re.DOTALL)
        example_slices = classify_slices(scheme_match.group(1) if scheme_match else "")

        n += 1
        for slice_name in example_slices:
            slices[slice_name]["n"] += 1
        if predicted is None:
            failures.append({"gold": gold, "raw": raw})
            continue
        n_parsed += 1

        if predicted.get("marks_awarded") == gold.get("marks_awarded"):
            n_exact_mark += 1
            for slice_name in example_slices:
                slices[slice_name]["n_exact_mark"] += 1

        pred_marks = predicted.get("marks_awarded")
        pred_mark_value = _mark_value_from_prompt(user_msg["content"])
        if (
            pred_mark_value is not None
            and isinstance(pred_marks, int)
            and pred_marks >= pred_mark_value
            and len(predicted.get("ebi", []) or []) > 0
        ):
            n_contradictory += 1
            for slice_name in example_slices:
                slices[slice_name]["n_contradictory"] += 1

        if (
            isinstance(pred_marks, int)
            and pred_marks == 0
            and len(predicted.get("www", []) or []) > 0
        ):
            n_contradictory_zero_marks_with_www += 1
            for slice_name in example_slices:
                slices[slice_name]["n_contradictory_zero"] += 1

        # Deterministic guardrail applied here too, mirroring
        # marking_engine.py/ai_pipeline.py - overlap scoring below should
        # reflect what a student actually sees in production (post-
        # correction), not the model's raw, sometimes self-contradictory
        # output. contradictory_rate/contradictory_zero_marks_rate above
        # stay computed on the RAW output since they're meant to track the
        # underlying model's own reliability, not what's shown post-fix.
        corrected_www = predicted.get("www", []) or []
        corrected_ebi = predicted.get("ebi", []) or []
        if isinstance(pred_marks, int) and pred_mark_value is not None and pred_marks >= pred_mark_value:
            corrected_ebi = []
        if isinstance(pred_marks, int) and pred_marks == 0:
            corrected_www = []

        overlap_www.append(
            overlap_score(corrected_www, gold.get("www", []))
        )
        overlap_ebi.append(
            overlap_score(corrected_ebi, gold.get("ebi", []))
        )
        overlap_misconceptions.append(
            overlap_score(predicted.get("misconception_tags", []), gold.get("misconception_tags", []))
        )

    slice_report = {
        name: {
            **stats,
            "exact_match_rate": stats["n_exact_mark"] / stats["n"] if stats["n"] else 0.0,
            "contradictory_rate": stats["n_contradictory"] / stats["n"] if stats["n"] else 0.0,
            "contradictory_zero_rate": stats["n_contradictory_zero"] / stats["n"] if stats["n"] else 0.0,
        }
        for name, stats in slices.items()
        if stats["n"] > 0
    }

    result = {
        "spec_slug": args.spec_slug,
        "model": args.model,
        "adapter_path": args.adapter_path,
        "eval_prompt_hash": current_hash,
        "n_test": n,
        "held_out_total": held_out_total,
        "sampled": sampled,
        "n_json_parsed": n_parsed,
        "json_parse_rate": n_parsed / n if n else 0.0,
        "marks_awarded_exact_match_rate": n_exact_mark / n_parsed if n_parsed else 0.0,
        "www_overlap_f1_mean": sum(overlap_www) / len(overlap_www) if overlap_www else 0.0,
        "ebi_overlap_f1_mean": sum(overlap_ebi) / len(overlap_ebi) if overlap_ebi else 0.0,
        "misconception_tags_overlap_f1_mean": sum(overlap_misconceptions) / len(overlap_misconceptions) if overlap_misconceptions else 0.0,
        "n_unparseable": len(failures),
        "n_contradictory_full_marks_with_ebi": n_contradictory,
        "contradictory_rate": n_contradictory / n_parsed if n_parsed else 0.0,
        "n_contradictory_zero_marks_with_www": n_contradictory_zero_marks_with_www,
        "contradictory_zero_marks_rate": n_contradictory_zero_marks_with_www / n_parsed if n_parsed else 0.0,
        "slices": slice_report,
    }
    print(json.dumps(result, indent=2))

    out_path = Path(args.out) if args.out else Path(args.adapter_path) / "eval_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({**result, "failures_sample": failures[:5]}, f, indent=2)
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
