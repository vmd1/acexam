import fitz  # PyMuPDF
import pdfplumber
import io
import re
import json
from typing import List, Dict, Any, Tuple
from image_extractor import extract_all_visuals_from_pdf
import ai_pipeline

async def run_full_ai_ingestion_pipeline(
    question_paper_bytes: bytes,
    mark_scheme_bytes: bytes | None = None,
    examiner_report_bytes: bytes | None = None,
    exam_board: str = "AQA",
    subject: str = "Biology",
    known_topics: List[Dict[str, str]] | None = None
) -> Dict[str, Any]:
    """
    Coordinates the complete multi-modal AI Ingestion Pipeline (§3.1, §3.1a, §6.2, §6.2a):
    1. Dual-mode visual extraction (raster + vector fallback) + sanity check
    2. Dual independent vision descriptions with agreement score
    3. pdfplumber table extraction
    4. Question boundary detection & stem image assignment
    5. Deterministic DSL compilation for 1-2 mark questions
    6. Examiner report misconception scanning
    7. Synthetic training answer generation & cross-check validation
    """
    pipeline_results = {
        "text_content": [],
        "images": [],
        "tables": [],
        "questions": [],
        "proposed_misconceptions": [],
        "synthetic_training_dataset": []
    }

    ai_pipeline.reset_token_usage()

    # 1. Visual Content Extraction (PyMuPDF Dual Mode + Sanity Check)
    extracted_images = extract_all_visuals_from_pdf(question_paper_bytes)
    
    # 2. Dual Independent Image Descriptions (§3.1a)
    for img in extracted_images:
        desc_a, desc_b, is_agreed, agreement_score = await ai_pipeline.generate_dual_image_descriptions(img)
        img["description_run_a"] = desc_a
        img["description_run_b"] = desc_b
        img["is_description_agreed"] = is_agreed
        img["description_agreement_score"] = agreement_score
        img["description"] = desc_a
        if not is_agreed:
            img["needs_review"] = True
            
    pipeline_results["images"] = extracted_images
    
    # 3. Text & Page Extraction
    doc = fitz.open(stream=question_paper_bytes, filetype="pdf")
    full_text_pages = [doc[i].get_text() for i in range(len(doc))]
    pipeline_results["text_content"] = full_text_pages
    full_text = "\n\n".join(full_text_pages)
    
    # 4. Table Extraction (pdfplumber)
    with pdfplumber.open(io.BytesIO(question_paper_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                pipeline_results["tables"].extend(tables)
                
    # 6. Parse Mark Scheme (if provided)
    mark_scheme_text = ""
    if mark_scheme_bytes:
        ms_doc = fitz.open(stream=mark_scheme_bytes, filetype="pdf")
        mark_scheme_text = "\n\n".join([ms_doc[i].get_text() for i in range(len(ms_doc))])

    # 5. Question Boundary Detection.
    # Real exam-board PDFs render question numbers with irregular character
    # spacing that regex can't reliably parse (verified: 2/25+ questions
    # found on a real AQA paper). Prefer AI-driven joint splitting, which
    # also isolates each question's own mark scheme text rather than the
    # whole document; fall back to regex if AI is unavailable/fails.
    ai_questions = await ai_pipeline.split_paper_into_questions(full_text_pages, mark_scheme_text, known_topics)
    using_ai_split = bool(ai_questions)
    if using_ai_split:
        raw_questions = ai_questions
    else:
        raw_questions = detect_question_boundaries(full_text)
    if not raw_questions:
        raw_questions = [{"number": "1(a)", "text": full_text[:400] if full_text else "Sample Question"}]

    prev_page = 1
    for idx, q in enumerate(raw_questions):
        if using_ai_split:
            mark_val = q["mark_value"]
            q_page = q["page"]
            per_question_scheme = q.get("mark_scheme_text") or ""
        else:
            # Extract mark value from text (e.g. [3 marks], [1 mark])
            mark_match = re.search(r'\[(\d+)\s*marks?\]', q["text"], re.IGNORECASE)
            mark_val = int(mark_match.group(1)) if mark_match else (2 if idx % 2 == 0 else 4)
            q_page = None
            per_question_scheme = ""

        # Mark value alone isn't a reliable signal - a 4-mark calculation
        # with one correct numeric answer (or an MCQ/grid-select question)
        # is just as deterministically gradable as a 1-mark question, via
        # the DSL's EXACT/RANGE/MCQ/ANY operators. Only genuinely open-ended
        # answers (free-text explanations, low mark_val as a proxy when the
        # AI split wasn't available to classify answer_type) fall to AI. A
        # "practical" question (draw/complete/label onto the paper itself)
        # has no typed answer at all, so it's neither DSL nor AI markable -
        # the student self-checks against the mark scheme instead.
        answer_type = q.get("answer_type", "written") if using_ai_split else "written"
        structured_answer = answer_type in ("numeric", "select", "multi_select", "grid_select")
        if answer_type == "practical":
            marking_type = "practical"
        else:
            marking_type = "dsl" if (mark_val <= 2 or structured_answer) else "ai"

        # Link images: page-aware when we have AI-assigned page numbers
        # (catches both same-page diagrams and shared stem diagrams
        # introduced on an earlier page since the previous question);
        # otherwise fall back to the old "first image" placeholder.
        # Only attach when the AI flagged this question as actually
        # referencing a figure/diagram/table - otherwise the page-range
        # heuristic tends to attach unrelated images to text-only questions
        # that merely share a page with a diagram for a neighbouring question.
        references_figure = q.get("references_figure", True) if using_ai_split else True
        if not references_figure:
            q_images = []
        elif q_page is not None:
            q_images = [img for img in extracted_images if prev_page <= img.get("page", 0) <= q_page]
        else:
            q_images = extracted_images[:1] if extracted_images else []

        if q_page is not None:
            prev_page = q_page

        # Caption images with the figure label this specific question uses
        # to refer to them (e.g. "Figure 9"), extracted by the AI splitter
        # from the question's own wording. A stem diagram can be shared by
        # several sub-questions that each refer to it differently, so copy
        # each image dict per-question rather than mutating the shared
        # extracted_images entries.
        figure_label = q.get("figure_label") if using_ai_split else _extract_figure_label(q["text"])
        if figure_label:
            q_images = [{**img, "caption": figure_label} for img in q_images]

        # Prefer the AI-isolated per-question mark scheme; fall back to the
        # whole document's mark scheme text if isolation didn't yield one.
        question_mark_scheme = per_question_scheme or mark_scheme_text

        # Compile Deterministic DSL for questions gradeable that way (1-2
        # marks, or a structured answer type at any mark value)
        dsl = None
        if marking_type == "dsl":
            dsl = await ai_pipeline.compile_mark_scheme_to_dsl(q["text"], mark_val, question_mark_scheme or q["text"], answer_type)

        # Synthetic dataset generation for 3+ mark questions (§6.2). Tagged
        # with this specific question's own AI-classified topic rather than
        # one admin-typed spec_code for the whole paper.
        synthetic_examples = []
        if marking_type == "ai":
            synthetic_examples = await ai_pipeline.generate_and_validate_synthetic_answers(
                question_text=q["text"],
                mark_value=mark_val,
                mark_scheme=question_mark_scheme or "Award marks for correct scientific reasoning.",
                spec_code=(q.get("topic_spec_code") if using_ai_split else None) or ""
            )
            # Tag each example with its own question's number so the caller
            # can resolve it to a question_id once questions are inserted
            # (this pipeline runs before any DB insert, so no id exists yet).
            for ex in synthetic_examples:
                ex["question_number"] = q["number"]
            pipeline_results["synthetic_training_dataset"].extend(synthetic_examples)

        pipeline_results["questions"].append({
            "question_number": q["number"],
            "mark_value": mark_val,
            "question_text": q["text"],
            "marking_type": marking_type,
            "marking_dsl": dsl,
            "mark_scheme_text": question_mark_scheme if question_mark_scheme else f"Official mark scheme rubric for Q{q['number']}",
            "images": q_images,
            "needs_review": any(img.get("needs_review") for img in q_images),
            # Best-matching spec_topics.spec_code for this specific question,
            # auto-classified by the AI splitter against the paper's known
            # topic list. None if unclassified (left uncategorized - flagged
            # via needs_review for admin attention).
            "topic_spec_code": q.get("topic_spec_code") if using_ai_split else None,
            "answer_type": answer_type,
            "answer_options": q.get("answer_options") if using_ai_split else None
        })
        
    # 7. Misconception Scanning (§6.2a) - scans whichever of the mark scheme
    # and examiner report are present (both optional uploads) and merges the
    # results, deduped by tag_id, so a misconception surfaced by both isn't
    # proposed twice. A mark scheme alone often reveals common wrong answers
    # via its "do not accept"/"common error" annotations even with no
    # separate examiner report uploaded.
    proposed_by_tag: Dict[str, Dict[str, Any]] = {}
    if mark_scheme_text:
        for tag in await ai_pipeline.scan_text_for_misconceptions(mark_scheme_text, "mark scheme", known_topics or []):
            proposed_by_tag[tag["tag_id"]] = tag
    if examiner_report_bytes:
        er_doc = fitz.open(stream=examiner_report_bytes, filetype="pdf")
        er_text = "\n\n".join([er_doc[i].get_text() for i in range(len(er_doc))])
        for tag in await ai_pipeline.scan_text_for_misconceptions(er_text, "examiner report", known_topics or []):
            proposed_by_tag[tag["tag_id"]] = tag
    pipeline_results["proposed_misconceptions"] = list(proposed_by_tag.values())

    pipeline_results["token_usage"] = ai_pipeline.get_token_usage()

    return pipeline_results

_FIGURE_LABEL_RE = re.compile(r'\b(Fig(?:ure)?\.?\s*\d+[a-z]?)\b', re.IGNORECASE)

def _extract_figure_label(text: str) -> str | None:
    """Regex fallback for the legacy (non-AI) boundary-detection path -
    pulls a "Figure 9" / "Fig. 2a" style reference straight out of the
    question text so images still get a caption when the AI splitter is
    unavailable."""
    match = _FIGURE_LABEL_RE.search(text or "")
    return match.group(1).strip() if match else None

def detect_question_boundaries(text: str) -> List[Dict[str, str]]:
    """
    Regex + heuristic boundary detection for official UK exam papers.
    Splits stems, sub-questions (e.g. 1(a), 1(b)(i), 3(c)).
    """
    pattern = re.compile(r'^\s*(\d+[a-z]?\s*\([ivx]+\)|\d+\s*\([a-z]\)|\d+\s*\.\s*)', re.MULTILINE | re.IGNORECASE)
    parts = pattern.split(text)
    
    questions = []
    current_q = None
    
    for part in parts:
        if pattern.match(part):
            current_q = re.sub(r'\s+', '', part.strip())
        elif current_q and part.strip():
            clean_text = part.strip()
            if len(clean_text) >= 10:
                questions.append({
                    "number": current_q,
                    "text": clean_text
                })
            current_q = None
            
    return questions
