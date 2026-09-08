"""
One-off: measures cold-start latency (model load from disk cache + first
generation) for a candidate base model, using the same marking prompt shape
evaluate.py uses. Run as a fresh process per model (not a loop over models
in one process) so "cold" actually means cold - mlx caches weights/graphs
once loaded, so a second model in the same process wouldn't reflect what a
real student hitting a freshly-loaded spec-code model experiences.
"""
import argparse
import json
import time
from pathlib import Path

from mlx_lm import generate, load

DATA_ROOT = Path(__file__).parent / "data"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--spec-slug", default="aqa-gcse-biology-higher")
    args = parser.parse_args()

    with open(DATA_ROOT / args.spec_slug / "test.jsonl") as f:
        example = json.loads(f.readline())
    system_msg, user_msg = example["messages"][0], example["messages"][1]

    t0 = time.perf_counter()
    model, tokenizer = load(args.model)
    load_time = time.perf_counter() - t0

    prompt = tokenizer.apply_chat_template([system_msg, user_msg], add_generation_prompt=True, tokenize=False)

    t0 = time.perf_counter()
    generate(model, tokenizer, prompt=prompt, max_tokens=512, verbose=False)
    first_gen_time = time.perf_counter() - t0

    t0 = time.perf_counter()
    generate(model, tokenizer, prompt=prompt, max_tokens=512, verbose=False)
    second_gen_time = time.perf_counter() - t0

    print(json.dumps({
        "model": args.model,
        "load_time_s": round(load_time, 2),
        "cold_time_s": round(load_time + first_gen_time, 2),
        "first_gen_time_s": round(first_gen_time, 2),
        "warm_gen_time_s": round(second_gen_time, 2),
    }, indent=2))


if __name__ == "__main__":
    main()
