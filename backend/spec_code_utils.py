"""
§6.1 spec-code identity shared between the training pipeline
(training/assemble_dataset.py, training/train_lora.py - which duplicate
this exact logic since they run in a separate venv/process, not imported
from here) and live marking (marking_engine.py, ai_pipeline.py). The slug
is what both the training/adapters/<slug>/ directory name and the Traefik
routing path prefix (/ai/mark/<slug>/...) are keyed on - if this drifts
from training's copy, a trained adapter's directory name won't match the
runtime path used to reach it.
"""


def spec_code_slug(exam_board: str, level: str, subject: str, tier: str | None) -> str:
    key = f"{exam_board}-{level}-{subject}-{tier or 'none'}"
    return key.lower().replace(" ", "_").replace("/", "-")
