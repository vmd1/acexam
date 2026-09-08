from typing import Any, List


def render_table_as_text(rows: List[List[Any]]) -> str:
    """Flattens a pdfplumber table (list of rows, each a list of cell strings,
    with None for a merged/empty cell) into plain text an LLM prompt can use
    - one row per line, cells separated by ' | ', empty cells rendered as ''.
    Shared by ingestion.py (compiling DSL / generating training examples)
    and marking_engine.py (live marking) so a question's table context is
    described identically wherever it's fed to a model."""
    return "\n".join(
        " | ".join((cell or "").strip() if isinstance(cell, str) else "" for cell in row)
        for row in rows
    )


def render_tables_as_text(tables: List[List[List[Any]]]) -> str:
    """Renders every table linked to a question, one after another."""
    return "\n\n".join(render_table_as_text(rows) for rows in tables)
