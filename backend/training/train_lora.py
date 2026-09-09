"""
§6.5 offline LoRA fine-tuning: takes a spec code's assembled dataset
(training/data/<slug>/{train,valid,test}.jsonl, from assemble_dataset.py)
and fine-tunes an ultra-lightweight base model with mlx_lm.lora.

Hyperparameters are chosen per run from the size of that spec code's own
training set (see pick_hyperparameters below), not fixed constants applied
to every subject. This matters because onboarding a new subject (e.g.
Physics) is meant to be "ingest papers, everything else automatic" (§6.5) -
a brand-new spec code might clear the auto-train threshold
(agent.py::auto_train_loop) with a few dozen examples, while a
long-ingested one might have thousands, and a single hand-tuned config
can't serve both well: too much capacity (rank) on a tiny dataset overfits
the synthetic generator's own quirks, too little on a large one leaves
signal on the table. There is no ground truth "optimal" here without
running a real held-out sweep per subject, which isn't scalable - these
are sound, documented defaults that move in the right direction
automatically as a subject's corpus grows, within §6.5's original starting
range (LoRA rank 16-32, LR 1e-4-2e-4, 2-3 epochs, effective batch ~16-32
via grad accumulation) and extending slightly below it (rank 8) only for
the smallest, just-past-threshold datasets where even rank 16 is more
capacity than the data supports.

Each run is version-tagged (never overwrites a prior adapter in place -
§6.5 "Versioning and serving") so a regression can be rolled back.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

# stdout is fully block-buffered (not line-buffered) when piped rather than
# attached to a tty - which is exactly how agent.py runs this script
# (subprocess.PIPE, read line-by-line to stream into training_jobs.log for
# the admin UI's live log view). Without this, every print() below sits in
# Python's internal buffer for minutes before flushing, so a real,
# healthy, in-progress run looks indistinguishable from a hung one.
sys.stdout.reconfigure(line_buffering=True)

_VAL_LOSS_RE = re.compile(r"^Iter (\d+): Val loss ([\d.]+)")

DATA_ROOT = Path(__file__).parent / "data"
ADAPTERS_ROOT = Path(__file__).parent / "adapters"

# Must stay in sync with routers/qualifications.py's DEFAULT_TRAINING_BASE_MODEL
# - both fall back to this when a job has no base_model set. Best-known
# result (94% exact-mark-match on aqa-gcse-biology-higher, job 143d9848)
# came from this model; a Qwen2.5-1.5B default briefly crept in
# undocumented and regressed a later retrain's accuracy 94% -> 84%, so pin
# it explicitly.
DEFAULT_MODEL = "mlx-community/Llama-3.2-3B-Instruct-4bit"
LORA_DROPOUT = 0.05
BATCH_SIZE_DEFAULT = 2  # safe on a 24GB Mac for models up to ~1.5B - see _batch_size_for_model below
NUM_LAYERS = -1  # all layers, appropriate for a 1.5B-3B base model
_MODEL_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)B", re.IGNORECASE)


def _batch_size_for_model(model: str) -> int:
    """
    A 3B base model roughly doubles a 1.5B model's memory footprint - at
    BATCH_SIZE_DEFAULT=2 (safe for 1.5B, ~14.6GB peak observed) a real 3B
    run pushed a 24GB Mac into swap (vm.swapusage showed 6GB+ resident, not
    just a hard OOM crash - the far worse failure mode, since it silently
    grinds to a near-halt for tens of minutes instead of failing fast).
    Halves the batch size for any model whose name states a parameter
    count of 3B or more; unrecognised/unlabeled model names fall back to
    the default rather than guessing a size. main() doubles
    grad_accumulation_steps to compensate, so the effective batch size
    (and therefore the learning rate schedule/training dynamics) stays the
    same regardless of which physical batch_size actually got used.
    """
    match = _MODEL_SIZE_RE.search(model)
    if match and float(match.group(1)) >= 3:
        return 1
    return BATCH_SIZE_DEFAULT
# The marking prompt/completion shape (question + mark scheme + student
# answer + JSON completion) tops out at 1057 tokens on the real corpus
# (measured directly against the base model's tokenizer, train+valid+test
# combined) - real crash seen at the old cap of 1024: one example's prompt
# alone (a lengthy "Indicative content" mark scheme) is 1030 tokens, so
# truncating to 1024 clipped its entire assistant-completion span, leaving
# zero unmasked loss tokens for that batch (mask_prompt's lower bound
# exceeded its upper bound) - a 0/0 division that produced a NaN loss.
# With grad_accumulation_steps > 1 that NaN gets summed into the
# accumulated gradient and permanently poisons every following step once
# it's applied, i.e. training never recovers on its own. This previously
# went unnoticed because batch_size=2 usually paired the outlier with
# another example, keeping the batch's total unmasked-token count above
# zero - batch_size=1 (forced for 3B+ models, see _batch_size_for_model)
# made it possible for the outlier to land alone in a batch. Re-measured
# after marking_engine.py's/assemble_dataset.py's system prompts grew
# substantially longer (the two-rule consistency block + worked examples +
# the misconception approved-tags list) - real corpus max became 1668
# tokens (up from 1057), so the old 1152 cap was now truncating over half
# the corpus rather than a rare outlier. Set with headroom above that new
# real max, still below mlx_lm's 2048 default.
#
# Re-measured again after the corpus grew from mostly-synthetic examples to
# mostly real mark-scheme-worked-exemplar/examiner-report-derived ones
# (ingestion.py's generate_mark_scheme_exemplar_examples / the examiner-
# exemplar path's reported_www/reported_ebi) - real, longer source
# documents push individual examples longer than invented ones did. New
# real corpus max is 1953 tokens; raised straight to mlx_lm's own 2048
# default rather than another narrow headroom bump, since that's already
# the ceiling this constant was deliberately kept under before.
#
# Re-measured again after the AQA/GCSE/Biology/Higher ingestion audit
# (multi-topic tagging, batched grading calls, MARKS:n: DSL) grew individual
# examples further - real corpus max is now 2439 tokens, already past the
# old 2048 cap (there's no lower ceiling left to stay under - mlx_lm has no
# fixed default beyond this). Raised with headroom above that new real max.
MAX_SEQ_LENGTH = 2688


@dataclass
class Hyperparameters:
    rank: int
    scale: float  # alpha (2x rank) / rank - mlx-lm's "scale" IS alpha/rank, not alpha itself, see tuner/lora.py
    learning_rate: float
    epochs: int
    grad_accumulation_steps: int  # effective batch = batch_size * this (batch_size is model-size-dependent, see _batch_size_for_model)


def pick_hyperparameters(n_train: int) -> Hyperparameters:
    """
    Scales LoRA rank/epochs/LR/effective-batch with training-set size so a
    freshly-onboarded subject with a small corpus and a long-ingested one
    with a large corpus each get a sensible config automatically - no
    per-subject manual tuning required. Thresholds are data-volume
    judgment calls documented above, not measured optima; revisit them if
    real eval_result.json exact-match rates show a systematic pattern
    across subjects (see evaluate.py's failures_sample for where to look
    first).
    """
    if n_train < 150:
        # Just past the auto-train threshold - little signal, high
        # overfitting risk. Lower rank than §6.5's documented floor (less
        # capacity than the data can justify), more epochs to get enough
        # optimizer steps at all (few steps/epoch with this little data),
        # a smaller effective batch so updates stay frequent, and a
        # slightly higher LR since there are few steps to converge in.
        return Hyperparameters(rank=8, scale=2.0, learning_rate=2e-4, epochs=4, grad_accumulation_steps=4)
    elif n_train < 600:
        # §6.5's original starting point.
        return Hyperparameters(rank=16, scale=2.0, learning_rate=1.5e-4, epochs=3, grad_accumulation_steps=8)
    else:
        # Enough data to justify more adapter capacity and a smoother
        # (larger) effective batch; fewer epochs since each epoch already
        # carries plenty of gradient signal - more would mostly just
        # memorize the synthetic generator's own phrasing quirks.
        return Hyperparameters(rank=32, scale=2.0, learning_rate=1e-4, epochs=2, grad_accumulation_steps=16)


def count_lines(path):
    with open(path) as f:
        return sum(1 for _ in f)


def preflight_check_sequence_lengths(data_dir: Path, model: str) -> None:
    """
    Tokenizes every train/valid/test example against the real target-model
    tokenizer and fails fast if any exceeds MAX_SEQ_LENGTH, instead of
    discovering it 1-2 iterations into a real training run via a NaN val
    loss. Real incident this prevents: a prompt-lengthening change pushed
    real corpus token length past the then-current cap without anyone
    re-measuring it, silently truncating over half the corpus (mask_prompt's
    unmasked-loss-token span for the clipped examples collapsed to zero,
    producing a 0/0 NaN once grad_accumulation_steps summed it in - which
    then permanently poisons every later training step). This is a ~10s
    check; the run it can save is 2-3 hours.
    """
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model)
    max_len = 0
    max_len_file = None
    n_over = 0
    for split in ("train.jsonl", "valid.jsonl", "test.jsonl"):
        path = data_dir / split
        if not path.exists():
            continue
        with open(path) as f:
            for line in f:
                messages = json.loads(line)["messages"]
                # tokenize=True on this transformers version returns a
                # BatchEncoding (len() = number of keys, not token count) -
                # render to text first, then tokenize and read input_ids.
                text = tokenizer.apply_chat_template(messages, tokenize=False)
                length = len(tokenizer(text)["input_ids"])
                if length > max_len:
                    max_len, max_len_file = length, split
                if length >= MAX_SEQ_LENGTH:
                    n_over += 1

    print(f"Preflight: max real example length {max_len} tokens (in {max_len_file}), cap is {MAX_SEQ_LENGTH}")
    if n_over > 0:
        raise SystemExit(
            f"Preflight check failed: {n_over} example(s) reach or exceed MAX_SEQ_LENGTH="
            f"{MAX_SEQ_LENGTH} tokens (longest is {max_len}, in {max_len_file}). Truncating any "
            f"example this way risks a 0/0 NaN val loss that never recovers (see this function's "
            f"docstring) - raise MAX_SEQ_LENGTH above {max_len} and rerun, rather than starting a "
            f"training job that would fail expensively partway through."
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("spec_slug", help="e.g. aqa-gcse-biology-higher (dir under training/data/)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--version", default=None, help="adapter version tag, defaults to a timestamp")
    args = parser.parse_args()

    data_dir = DATA_ROOT / args.spec_slug
    if not (data_dir / "train.jsonl").exists():
        print(f"No train.jsonl in {data_dir} - run assemble_dataset.py first", file=sys.stderr)
        sys.exit(1)

    preflight_check_sequence_lengths(data_dir, args.model)

    n_train = count_lines(data_dir / "train.jsonl")
    hp = pick_hyperparameters(n_train)
    batch_size = _batch_size_for_model(args.model)
    if batch_size != BATCH_SIZE_DEFAULT:
        # Preserve the effective batch size (batch_size * grad_accumulation_steps)
        # pick_hyperparameters chose for this dataset size, regardless of
        # which physical batch_size memory constraints forced - otherwise a
        # smaller physical batch would silently shrink the effective batch
        # too, changing the training dynamics pick_hyperparameters tuned for.
        grad_accumulation_steps = hp.grad_accumulation_steps * (BATCH_SIZE_DEFAULT // batch_size)
    else:
        grad_accumulation_steps = hp.grad_accumulation_steps
    steps_per_epoch = max(1, -(-n_train // batch_size))  # ceil
    iters = steps_per_epoch * hp.epochs

    version = args.version or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    adapter_path = ADAPTERS_ROOT / args.spec_slug / f"v-{version}"
    adapter_path.mkdir(parents=True, exist_ok=True)

    config = {
        "model": args.model,
        "train": True,
        "fine_tune_type": "lora",
        "data": str(data_dir),
        "seed": 42,
        "num_layers": NUM_LAYERS,
        "batch_size": batch_size,
        "iters": iters,
        "val_batches": -1,
        "learning_rate": hp.learning_rate,
        # "4 reports per epoch" (steps_per_epoch // 4) made sense when every
        # run was fast, but it scales with dataset size, not with how slow
        # each iteration actually is - on a bigger/slower base model
        # (3B + grad_checkpoint) that stretched to 20+ minutes with zero
        # feedback before the first report. Cap it at a small fixed count
        # instead, so reporting frequency reflects wall-clock time, not
        # iteration count - a DB log write per report is cheap regardless.
        # Report every iteration: the DB write is negligible next to a
        # 10-30s+ training step, and per-iteration visibility is worth more
        # than the log volume it costs, especially on a slow/large model.
        "steps_per_report": 1,
        "steps_per_eval": steps_per_epoch,
        "adapter_path": str(adapter_path),
        # Checkpoint at every eval point (not just the final iter) so the
        # best-val-loss checkpoint below actually has a saved file to
        # promote - small datasets (§6.5 "high overfitting risk" territory)
        # routinely see val loss trough mid-run and climb back up before
        # the final iter, and the final iter is not always the best one.
        "save_every": steps_per_epoch,
        "max_seq_length": MAX_SEQ_LENGTH,
        "grad_accumulation_steps": grad_accumulation_steps,
        # Recomputes activations during the backward pass instead of keeping
        # them all resident - real observed failure without this: a 3B base
        # model at rank 16 (this spec code's auto-picked hyperparameters for
        # its dataset size) OOM'd on a 24GB Mac's unified memory mid-training
        # (Metal "Insufficient Memory") right after the first iteration.
        # Costs some training wall-clock time in exchange for a real memory
        # cut, which is the right trade for an offline training job.
        "grad_checkpoint": True,
        "mask_prompt": True,  # loss only on the assistant's JSON completion, not the prompt
        "lora_parameters": {
            "rank": hp.rank,
            "dropout": LORA_DROPOUT,
            "scale": hp.scale,
        },
    }

    config_path = adapter_path / "lora_config.yaml"
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)

    meta = {
        "spec_slug": args.spec_slug,
        "base_model": args.model,
        "version": version,
        "train_examples": n_train,
        "iters": iters,
        "hyperparameters": {
            "rank": hp.rank, "scale": hp.scale, "learning_rate": hp.learning_rate,
            "epochs": hp.epochs, "batch_size": batch_size,
            "grad_accumulation_steps": grad_accumulation_steps,
        },
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(adapter_path / "run_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    print(f"Training {args.spec_slug} v-{version}: {n_train} train examples, "
          f"rank {hp.rank}, lr {hp.learning_rate}, {iters} iters "
          f"({hp.epochs} epochs @ batch {batch_size}, grad_accum {grad_accumulation_steps}) -> {adapter_path}")

    # Stream mlx_lm's own stdout through unmodified (so it's still watchable
    # live exactly as before) while also parsing its "Iter N: Val loss X"
    # lines to track which checkpoint actually generalized best - mlx_lm
    # itself only ever saves periodic + final checkpoints, it has no
    # best-val-loss tracking of its own.
    proc = subprocess.Popen(
        [sys.executable, "-m", "mlx_lm", "lora", "--config", str(config_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    val_losses = []  # [(iteration, val_loss), ...] in report order
    for line in proc.stdout:
        print(line, end="")
        m = _VAL_LOSS_RE.match(line.strip())
        if m:
            val_losses.append((int(m.group(1)), float(m.group(2))))
    returncode = proc.wait()
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, proc.args)

    # Promote the best-val-loss checkpoint over the final one, if it isn't
    # already the final one. Excludes iteration 1 (the pre-training
    # baseline, evaluated before any weight update - no checkpoint file
    # exists for it, since save_every never divides 1).
    candidates = [(it, vl) for it, vl in val_losses if it > 1]
    best_info = None
    if candidates:
        best_iter, best_val_loss = min(candidates, key=lambda x: x[1])
        best_checkpoint = adapter_path / f"{best_iter:07d}_adapters.safetensors"
        final_val_loss = candidates[-1][1]
        adapter_file = adapter_path / "adapters.safetensors"
        if best_checkpoint.exists() and best_iter != iters:
            shutil.copyfile(best_checkpoint, adapter_file)
            print(
                f"Best checkpoint was iter {best_iter} (val loss {best_val_loss:.3f}) "
                f"vs. final iter {iters} (val loss {final_val_loss:.3f}) - "
                f"promoted iter {best_iter}'s weights to {adapter_file}."
            )
        else:
            print(
                f"Final iter {iters} (val loss {final_val_loss:.3f}) was also "
                f"the best checkpoint - no promotion needed."
            )
        best_info = {
            "best_iteration": best_iter,
            "best_val_loss": best_val_loss,
            "final_val_loss": final_val_loss,
            "promoted": best_checkpoint.exists() and best_iter != iters,
        }

    if best_info:
        meta["eval"] = best_info
        with open(adapter_path / "run_meta.json", "w") as f:
            json.dump(meta, f, indent=2)

    print(f"Done. Adapter saved to {adapter_path}")
    print(f"Never overwritten in place (§6.5) - next run gets its own version tag.")


if __name__ == "__main__":
    main()
