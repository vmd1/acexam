import fitz  # PyMuPDF
import pdfplumber
import hashlib
import io
import re
import json
from typing import List, Dict, Any, Tuple, Optional
from image_extractor import extract_all_visuals_from_pdf
import ai_pipeline
import marking_engine
import storage
import table_utils

_EXT_CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


_ROOT_NUMBER_RE = re.compile(r'^0*(\d+)')


def _question_root(question_number: str) -> str:
    """Mirrors routers/generate.py's _question_root / PracticeSession.tsx's
    questionRootKey exactly, so sub-questions like '08.1'/'08.2'/'08.3' are
    recognised here as siblings of the same stem the same way the frontend
    groups them into one rendered page."""
    match = _ROOT_NUMBER_RE.match(question_number or '')
    return match.group(1) if match else (question_number or '')


def _is_real_table(rows: List[List[Any]]) -> bool:
    """
    Filters pdfplumber's page.extract_tables() output down to things that
    are actually tables - see the call site's comment for the real garbage
    this catches (boxed question-number glyphs misdetected as a 1x2 "table",
    a whole page's text as one giant 1-cell "table"). Requires at least 2
    rows and 2 columns, and that a real share of cells hold more than a
    stray character - a real data table's cells mostly have actual content,
    unlike a mis-parsed ruling/border region.
    """
    if not rows or len(rows) < 2 or len(rows[0]) < 2:
        return False
    cells = [cell for row in rows for cell in row]
    if not cells:
        return False
    non_empty = sum(1 for c in cells if isinstance(c, str) and c.strip())
    return (non_empty / len(cells)) >= 0.3


_FIGURE_LABEL_NUM_RE = re.compile(r'(\d+[a-z]?)', re.IGNORECASE)


def _normalize_figure_label(label: Optional[str]) -> Optional[str]:
    """Loosely normalizes a figure_label so 'Figure 2', 'Fig. 2', 'figure  2'
    etc. compare equal - used to match a specific sub-question's own
    figure_label against a specific extracted image (see the group image
    linking pre-pass below), not just deduped for display."""
    if not label:
        return None
    m = _FIGURE_LABEL_NUM_RE.search(label)
    return m.group(1).lower() if m else label.strip().lower()


SYNTHETIC_SAMPLE_RATE_LOW_MARK = 1 / 5  # applies to mark_value <= 2 only

_PARTIAL_CREDIT_RE = re.compile(r'\ballow(?:ed)?\s+(?:for\s+)?(\d+)\s+marks?\b', re.IGNORECASE)


def _has_partial_credit_alternative(mark_scheme_text: str, mark_value: int) -> bool:
    """
    True when the mark scheme explicitly states an alternate answer is worth
    FEWER marks than this question's full mark_value (e.g. "allow for 1 mark
    an answer of 7 / 7.2 with evidence of having used 3% from Figure 1" on a
    3-mark question) - the recurring UK mark-scheme pattern for crediting a
    wrong-method/misread answer with partial marks. A numeric DSL clause is
    evaluated as one all-or-nothing boolean (marking_engine.
    evaluate_dsl_expression), and mark_question's DSL path awards the full
    mark_value on any pass - OR-ing such a partial-credit alternate value
    into the same clause as the full-credit answer would silently award full
    marks to a wrong answer the mark scheme itself only credits partially
    (unlike select/multi_select, which already has real partial credit via
    _mcq_partial_credit's per-option counting). Only meaningful above 1 mark
    (nothing to award "fewer" marks than). Not real NLP - just checking for
    the specific "allow ... for N mark(s)" phrasing exam boards use for this,
    so a false negative just leaves a question on the DSL path as before
    (unchanged risk); a false positive just costs an unnecessary AI-marking
    routing rather than a wrong deterministic one.
    """
    if mark_value <= 1 or not mark_scheme_text:
        return False
    for m in _PARTIAL_CREDIT_RE.finditer(mark_scheme_text):
        try:
            if int(m.group(1)) < mark_value:
                return True
        except ValueError:
            continue
    return False


def _samples_for_synthetic_generation(question_text: str, mark_value: int) -> bool:
    """
    Whether this question gets synthetic training answers generated (§6.2).
    Every 3+ mark question always does - they're rarer and vary more in
    structure, so full coverage is worth the cost. 1-2 mark questions are
    the majority of the written bank but each only has 2-3 possible mark
    levels, so generating for all of them buys comparatively little extra
    training signal for the LLM spend - deterministically sample a fraction
    instead, keyed off a hash of the question's own text so the same
    question is consistently included or excluded across re-ingestions
    regardless of its position in the paper.
    """
    if mark_value > 2:
        return True
    digest = hashlib.md5(question_text.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < SYNTHETIC_SAMPLE_RATE_LOW_MARK * 100

async def run_full_ai_ingestion_pipeline(
    question_paper_bytes: bytes,
    mark_scheme_bytes: bytes | None = None,
    examiner_report_bytes: bytes | None = None,
    exam_board: str = "AQA",
    subject: str = "Biology",
    known_topics: List[Dict[str, str]] | None = None,
    known_misconceptions: List[Dict[str, str]] | None = None,
    existing_taxonomy: List[Dict[str, str]] | None = None
) -> Dict[str, Any]:
    """
    Coordinates the complete multi-modal AI Ingestion Pipeline (§3.1, §3.1a, §6.2, §6.2a):
    1. Dual-mode visual extraction (raster + vector fallback) + sanity check
    2. Dual independent vision descriptions with agreement score
    3. pdfplumber table extraction
    4. Question boundary detection & stem image assignment
    5. Deterministic DSL compilation for 1-2 mark questions
    6. Synthetic training answer generation & cross-check validation
    7. Real exemplar answer extraction from the examiner report & cross-check validation
    8. Examiner report / mark scheme misconception scanning
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

    # 1. Visual Content Extraction (PyMuPDF Dual Mode + Sanity Check).
    # extract_all_visuals_from_pdf is sync/CPU-bound and returns each
    # image's raw bytes rather than writing to local disk (see its own
    # docstring) - upload each to S3/MinIO here, the first point in the
    # pipeline that's actually running inside an event loop, and replace
    # the placeholder with the real public URL before anything downstream
    # (which JSON-serializes these dicts straight into the DB) sees them.
    extracted_images = extract_all_visuals_from_pdf(question_paper_bytes)
    for img in extracted_images:
        key = img.pop("key")
        img["url"] = await storage.upload_bytes(
            f"question-images/{key}", img["_bytes"], _EXT_CONTENT_TYPES.get(img.get("ext", "png"), "application/octet-stream")
        )

    # 2. Dual Independent Image Descriptions (§3.1a) - needs the actual
    # image bytes (still held in "_bytes" from extraction above, not popped
    # until below) rather than re-reading from disk; nothing is written to
    # local disk any more now that images live in S3/MinIO.
    for img in extracted_images:
        desc_a, desc_b, is_agreed, agreement_score = await ai_pipeline.generate_dual_image_descriptions(img, img_bytes=img["_bytes"])
        img["description_run_a"] = desc_a
        img["description_run_b"] = desc_b
        img["is_description_agreed"] = is_agreed
        img["description_agreement_score"] = agreement_score
        img["description"] = desc_a
        if not is_agreed:
            img["needs_review"] = True
        del img["_bytes"]

    pipeline_results["images"] = extracted_images
    
    # 3. Text & Page Extraction
    doc = fitz.open(stream=question_paper_bytes, filetype="pdf")
    full_text_pages = [doc[i].get_text() for i in range(len(doc))]
    pipeline_results["text_content"] = full_text_pages
    full_text = "\n\n".join(full_text_pages)
    
    # 4. Table Extraction (pdfplumber). Page number recorded (1-indexed, same
    # convention as image_extractor.py's "page" field) so each table can be
    # linked to the question(s) that reference it below, the same way
    # extracted images are.
    #
    # pdfplumber's page.extract_tables() is prone to false positives on real
    # exam-paper layouts: a ruled answer box or the boxed "0 1 . 2"-style
    # question-number glyphs (see the CLAUDE.md-documented irregular
    # character spacing) get misdetected as tiny 1-row tables, and a whole
    # page's question text inside one bordered region as a single giant
    # 1-cell "table" - confirmed against real ingested data (a 48-question
    # paper produced 68 "tables", the vast majority literally [["0", "1"]]
    # or one cell holding the entire page's text). None of that is a real
    # data table, but it still gets rendered into every relevant question's
    # LLM context below - pure noise that both degrades marking-scheme/DSL
    # compilation quality and multiplies token cost across every per-question
    # call. _is_real_table requires actual multi-row, multi-column, mostly
    # non-trivial content before a detected table is trusted at all.
    with pdfplumber.open(io.BytesIO(question_paper_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if _is_real_table(table):
                    pipeline_results["tables"].append({"page": page.page_number, "rows": table})
                
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

    # Pre-pass: group sub-questions sharing one stem (e.g. 08.1/08.2/08.3) by
    # their leading question number, mirroring generate.py's _question_root /
    # PracticeSession.tsx's questionRootKey - these siblings are always
    # rendered together as one page (ExamCanvas takes a whole group). This
    # group's overall page span is used below ONLY as the search window for
    # a sub-question that references a figure/table itself but can't be
    # confidently matched to one specific image/table (see
    # group_label_to_image below) - never to hand a sub-question something
    # it never referenced at all (that was the previous, now-removed
    # behavior, and it badly over-attached in practice - see the per-
    # question linking block further down for what replaced it).
    group_page_span: Dict[str, Tuple[int, int]] = {}
    if using_ai_split:
        pages_by_root: Dict[str, List[int]] = {}
        for q in raw_questions:
            root = _question_root(q["number"])
            pages_by_root.setdefault(root, []).append(q["page"])
        group_page_span = {root: (min(pages), max(pages)) for root, pages in pages_by_root.items()}

    # Second pre-pass: a group can contain MORE THAN ONE distinct diagram
    # (e.g. sub-questions 2.2-2.4 discuss "Figure 2" while 2.5-2.7 discuss a
    # completely different "Figure 3") - the blanket group-page-range
    # linking above would attach BOTH images to EVERY sub-question in the
    # group, and then caption BOTH of them with whichever single sub-
    # question's figure_label happened to be assigned to a checksum first
    # (confirmed real case: two distinct images both captioned "Figure 2",
    # one of them actually "Figure 3", every sub-question showing both).
    # Disambiguate whenever a group's sub-questions between them name as
    # many DISTINCT figure labels as there are images in the group's page
    # range - real papers print/discuss multiple diagrams under one stem in
    # the same order they're referenced, so zipping labels-in-first-seen-
    # order against images-in-page-order is a reasonable pairing. Only
    # trusted when the counts line up exactly; any other case (one shared
    # diagram referenced by every sibling - the ORIGINAL motivating case for
    # group-level sharing, or a genuinely ambiguous mismatch) falls back to
    # the blanket behavior below rather than guessing a wrong pairing.
    group_label_to_image: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if using_ai_split:
        labels_by_root: Dict[str, List[str]] = {}
        for q in raw_questions:
            root = _question_root(q["number"])
            # Same regex backstop as the per-question loop below - a label
            # only ever visible via an explicit "Figure N" mention in one
            # sub-question's own text (never surfaced through the AI's own
            # figure_label field on ANY sibling) must still be able to seed
            # the group's disambiguation map, or a group where every member
            # relies on this backstop would never get one built at all.
            norm = _normalize_figure_label(q.get("figure_label") or _extract_figure_label(q["text"]))
            if norm and norm not in labels_by_root.setdefault(root, []):
                labels_by_root[root].append(norm)
        for root, labels in labels_by_root.items():
            if len(labels) < 2 or root not in group_page_span:
                continue
            lo, hi = group_page_span[root]
            images_in_range = sorted(
                (img for img in extracted_images if lo <= img.get("page", 0) <= hi),
                key=lambda img: img.get("page", 0),
            )
            if len(images_in_range) == len(labels):
                group_label_to_image[root] = dict(zip(labels, images_in_range))

    # Once a physical image (identified by its content checksum, so the same
    # diagram reused across sibling sub-questions is recognised as the same
    # object even though each gets its own shallow-copied dict) has been
    # captioned for one question, every later question that also links it
    # keeps that same caption - rather than trusting each sub-question's own
    # AI-detected figure_label independently, which is how the same shared
    # stem diagram ended up captioned "Figure 7" on one sub-question and
    # "Figure 6" on the very next (both referring to the one image).
    caption_by_checksum: Dict[str, str] = {}
    misconceptions_from_grading: Dict[str, Dict[str, Any]] = {}
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

        # DSL is only used for answer types with one deterministic, structurally
        # fixed correct value - numeric/select/multi_select/grid_select - where
        # exact/option matching is reliable. Every free-text "written" answer
        # (regardless of mark value) goes through AI marking instead: keyword/
        # phrase-matching DSL operators (CONTAIN/ANY/ALL/MIN) proved too brittle
        # on short reasoning answers, false-negatively zeroing genuinely correct
        # paraphrased science (e.g. a 1-mark "Explain" answer that used different
        # wording for the same accepted point). A "practical" question (draw/
        # complete/label onto the paper itself) has no typed answer at all, so
        # it's neither DSL nor AI markable - the student self-checks against the
        # mark scheme instead.
        answer_type = q.get("answer_type", "written") if using_ai_split else "written"
        structured_answer = answer_type in ("numeric", "select", "multi_select", "grid_select")
        _scheme_for_routing = per_question_scheme or mark_scheme_text
        if answer_type == "practical":
            marking_type = "practical"
        elif structured_answer:
            marking_type = "dsl"
        else:
            marking_type = "ai"

        # §6.2 real exemplar-answer eligibility (from the examiner report).
        # Every free-text "written" question is now AI-marked regardless of
        # mark value (§ marking_type above), so every one benefits from real
        # exemplar answers as training signal - including 1-mark questions,
        # which used to be excluded back when they were graded by
        # deterministic DSL instead. Structured/select/numeric questions are
        # still excluded (no free-text answer variety to capture there).
        exemplar_eligible = marking_type == "ai"

        # Link images/tables PER SUB-QUESTION, gated strictly on that
        # specific sub-question's own references_figure flag (the AI
        # splitter's field name covers "a diagram, image, graph, table, or
        # figure" - not just pictures). Previously this attached everything
        # in the whole group's page range to EVERY sibling whenever ANY ONE
        # of them referenced a figure/table - intended to stop a shared stem
        # diagram from going missing on follow-on sub-questions, but
        # confirmed in real ingested data to badly over-attach instead: e.g.
        # three unrelated real data tables (sugar/obesity, iodine colour,
        # enzyme pH) all glued onto every one of 7 sibling sub-questions
        # that each only needed ONE of them, and a diagram showing on
        # sub-questions whose own text never mentions it at all. A
        # sub-question that doesn't reference a figure/table of its own
        # gets none, full stop - no group-wide fallback.
        # Non-AI-split fallback keeps the old "first image" placeholder,
        # since there's no reliable page/grouping info in that path.
        root = _question_root(q["number"]) if using_ai_split else None
        figure_label = q.get("figure_label") if using_ai_split else _extract_figure_label(q["text"])
        own_references_figure = bool(q.get("references_figure", False)) if using_ai_split else True
        # Backstop against the AI splitter under-classifying one specific
        # sub-question: a deterministic regex scan of THIS sub-question's own
        # printed text for an explicit "Figure N" mention (real case found in
        # ingested data - a sub-question's own text read "Figure 11 shows the
        # results" verbatim, yet the splitter still returned
        # references_figure=false for it while correctly flagging its
        # sibling). Strict per-question gating means a miss like that now
        # means a genuinely missing image rather than being silently papered
        # over by the old whole-group fallback, so an explicit textual
        # mention overrides the AI's own classification rather than only
        # supplementing it.
        if using_ai_split:
            regex_label = _extract_figure_label(q["text"])
            if regex_label:
                own_references_figure = True
                figure_label = figure_label or regex_label
        own_label_norm = _normalize_figure_label(figure_label) if using_ai_split else None
        label_map = group_label_to_image.get(root) if using_ai_split else None

        # Tables have no equivalent of images' figure_label/checksum
        # disambiguation - pdfplumber's extract_tables() (step 4 above) only
        # ever hands back cell text plus the single page it was found on, no
        # caption/label text to match against a sub-question's own wording.
        # But each AI-split sub-question DOES carry its own "page" (q_page,
        # captured above) - the page its own question_text was actually
        # extracted from, which is a much tighter scope than the whole
        # sibling group's [lo, hi] page span. A real multi-table group (e.g.
        # an obesity/sugar-intake table, an iodine-colour table, a protease-
        # pH table across sub-questions 5.1-5.7) prints each table on/near
        # the page of the specific sub-question that actually uses it, so
        # matching on q_page alone is enough to stop unrelated tables
        # elsewhere in the group from being attached. Only widen to the
        # group-wide range (the old, coarser behavior) when nothing was
        # found on this exact page - a table can still be printed a page
        # before/after the sub-question that references it (e.g. a shared
        # stem table printed once above several sub-questions spanning more
        # than one page) - so the group-wide range stays as the rare
        # fallback, not the default.
        q_page_tables = (
            [t["rows"] for t in pipeline_results["tables"] if t.get("page") == q_page]
            if using_ai_split and q_page is not None else []
        )

        if using_ai_split and not own_references_figure:
            q_images = []
            q_tables = []
        elif using_ai_split and label_map and own_label_norm and own_label_norm in label_map:
            # Disambiguated (see the group_label_to_image pre-pass above) -
            # this sub-question names a specific figure the group's images
            # could be confidently paired against, so it gets ONLY that one
            # image, not every image anywhere in the group's page range.
            q_images = [label_map[own_label_norm]]
            if q_page_tables:
                q_tables = q_page_tables
            else:
                lo, hi = group_page_span[root]
                q_tables = [t["rows"] for t in pipeline_results["tables"] if lo <= t.get("page", 0) <= hi]
        elif using_ai_split and root in group_page_span:
            # This sub-question DOES reference a figure/table of its own,
            # but not one we could confidently pick out individually via the
            # group_label_to_image pre-pass (no named label, or the group's
            # distinct-label count didn't exactly match its image count -
            # e.g. two genuinely different diagrams both mislabelled
            # "Figure 9" by the AI splitter/source PDF, which is exactly the
            # count mismatch that pre-pass refuses to guess through).
            #
            # The OLD behavior here attached every image anywhere in the
            # group's page range to every sub-question that referenced any
            # figure at all - confirmed to reproduce the reported bug
            # exactly: two distinct "Figure 9" images both landing on both
            # of the two sub-questions that each only needed one of them,
            # while a third sibling needing a different diagram effectively
            # never got a chance to be distinguished from the other two.
            #
            # Narrow this per-question instead of per-group: prefer the
            # image(s) that actually sit on THIS sub-question's own page. A
            # single same-page match is about as strong a signal as we have
            # without real per-image labels (image_extractor.py extracts
            # "page"/"bbox"/"checksum" per image but never an OCR'd
            # caption), since real papers place a diagram immediately next
            # to (or on) the sub-question that discusses it. Multiple
            # same-page candidates are still genuinely ambiguous - guessing
            # between them is exactly the wrong-image-is-worse-than-no-image
            # case the issue calls out, so that yields nothing rather than a
            # coin flip.
            lo, hi = group_page_span[root]
            candidates = [img for img in extracted_images if lo <= img.get("page", 0) <= hi]
            if len(candidates) <= 1:
                # 0 or 1 candidate in the whole group range is unambiguous
                # by construction - this is also how a single diagram shared
                # by every sibling under one stem still reaches every one of
                # them, unchanged from before.
                q_images = candidates
            else:
                same_page = [img for img in candidates if q_page is not None and img.get("page") == q_page]
                if len(same_page) == 1:
                    q_images = same_page
                elif len(same_page) > 1:
                    q_images = []
                elif q_page is not None:
                    # Nothing on this exact page - fall back to the single
                    # nearest candidate by page distance, but only if it
                    # isn't tied with another equally-near candidate.
                    nearest_dist = min(abs(img.get("page", 0) - q_page) for img in candidates)
                    nearest = [img for img in candidates if abs(img.get("page", 0) - q_page) == nearest_dist]
                    q_images = nearest if len(nearest) == 1 else []
                else:
                    q_images = []
            # Tables: prefer this sub-question's own page (q_page_tables,
            # see above) over the whole group's range - the group-wide
            # range is now the rare last-resort fallback, not the default.
            q_tables = q_page_tables if q_page_tables else [t["rows"] for t in pipeline_results["tables"] if lo <= t.get("page", 0) <= hi]
        elif not using_ai_split and q.get("references_figure", True):
            q_images = extracted_images[:1] if extracted_images else []
            q_tables = [t["rows"] for t in pipeline_results["tables"]][:1] if pipeline_results["tables"] else []
        else:
            q_images = []
            q_tables = []

        # Caption images with the figure label this specific question uses
        # to refer to them (e.g. "Figure 9"), extracted by the AI splitter
        # from the question's own wording. A stem diagram can be shared by
        # several sub-questions that each refer to it differently, so copy
        # each image dict per-question rather than mutating the shared
        # extracted_images entries. The first caption assigned to a given
        # physical image (by checksum) wins for every later question that
        # links the same image, instead of re-trusting each sub-question's
        # own possibly-inconsistent figure_label detection - safe now that
        # disambiguated images above are only ever paired with the ONE
        # sub-question whose own label actually matched them, so a group's
        # first-processed sibling can no longer stamp its own label onto a
        # DIFFERENT image it merely happened to share a page range with.
        resolved_images = []
        for img in q_images:
            checksum = img.get("checksum")
            existing_caption = caption_by_checksum.get(checksum) if checksum else None
            caption = existing_caption or figure_label
            if caption and checksum and not existing_caption:
                caption_by_checksum[checksum] = caption
            resolved_images.append({**img, "caption": caption} if caption else img)
        q_images = resolved_images

        # Prefer the AI-isolated per-question mark scheme; fall back to the
        # whole document's mark scheme text if isolation didn't yield one.
        question_mark_scheme = per_question_scheme or mark_scheme_text

        # Many sub-questions depend on shared context printed once above the
        # group (an experiment/method description, a scenario, background
        # data) rather than repeated in each sub-question's own wording -
        # e.g. "Explain the results at 30C and at 90C" is meaningless without
        # knowing which investigation it refers to. The AI splitter captures
        # that as stem_text, identical across every sibling sub-question;
        # fold it into this question's own working text (DSL compilation,
        # synthetic answer generation, and what's actually stored/shown all
        # need the same context a real student reading the paper would have)
        # rather than losing it the way "just this sub-question's text"
        # extraction used to.
        stem_text = (q.get("stem_text") or "").strip() if using_ai_split else ""
        full_text = f"{stem_text}\n\n{q['text']}" if stem_text else q["text"]
        # A question like "explain the anomaly at 30C (see Table 1)" is
        # meaningless - and a numeric DSL question referencing table values
        # is literally unanswerable - without the table's own data. Every
        # LLM call below needs that context, but it must NOT be folded into
        # the stored/displayed question_text (full_text) itself: the
        # frontend now renders a linked table once, as its own proper block
        # shared across the whole sub-question group (see table_data below)
        # - dumping the raw cell text into every sibling's own question_text
        # would just duplicate it, once per sub-question, as an unreadable
        # pipe-separated wall of text.
        llm_context_text = full_text
        if q_tables:
            llm_context_text += "\n\n" + table_utils.render_tables_as_text(q_tables)

        # Compile Deterministic DSL for questions gradeable that way (1-2
        # marks, or a structured answer type at any mark value)
        dsl = None
        dsl_problems: List[str] = []
        if marking_type == "dsl":
            dsl = await ai_pipeline.compile_mark_scheme_to_dsl(llm_context_text, mark_val, question_mark_scheme or llm_context_text, answer_type, q.get("answer_options"))
            # Nothing else parses this DSL until a real student submits an
            # answer against it - a malformed clause would otherwise fail
            # (or wrongly pass) silently in production with zero admin
            # visibility. Catch it here instead and route to review.
            dsl_problems = marking_engine.validate_dsl_syntax(dsl)
            if dsl_problems:
                print(f"Q{q['number']}: marking_dsl failed validation, flagging for review: {dsl_problems}")
            # A numeric mark scheme that explicitly credits a wrong-method
            # alternate answer with FEWER marks than full credit (see
            # _has_partial_credit_alternative) MUST come back with a
            # MARKS:n: wrapper on that alternate's branch - a plain OR would
            # award it full marks instead (the exact Q01.6 bug this operator
            # exists to fix). If the compiler didn't use it despite the
            # scheme clearly calling for it, don't trust the DSL silently;
            # flag for admin review instead.
            elif answer_type == "numeric" and _has_partial_credit_alternative(_scheme_for_routing, mark_val) and "marks:" not in dsl.lower():
                dsl_problems = [
                    "Mark scheme states a lower-mark alternate answer, but the compiled DSL has no "
                    "MARKS:n: clause for it - it may silently award full marks to a partial-credit answer."
                ]
                print(f"Q{q['number']}: {dsl_problems[0]}")

        # Synthetic dataset generation for AI-marked (free-text "written")
        # questions (§6.2) - this is real spend on a generation call to
        # invent an answer from scratch, so it stays scoped to questions
        # that actually need AI marking at runtime (structured/select/
        # numeric/practical questions never call this). 1-2 mark questions
        # are the large majority of the written question bank but only have
        # 2-3 possible mark levels each (vs. up to 6 for higher-mark
        # questions), so full per-question coverage there buys much less
        # training signal per LLM call than it costs - _samples_for_synthetic_generation
        # deterministically includes half of them (every 3+ mark question
        # still gets full coverage). Tagged with this specific question's
        # own AI-classified topic rather than one admin-typed spec_code for
        # the whole paper.
        # A question can now match several spec topics (see topic_spec_codes
        # below) - the per-question training-example calls below only tag
        # examples with ONE spec_code string for context/labelling, so use
        # the first (the AI splitter's best/primary match) rather than
        # picking arbitrarily or joining them into one malformed code.
        topic_spec_codes = (q.get("topic_spec_codes") or []) if using_ai_split else []
        primary_topic_code = topic_spec_codes[0] if topic_spec_codes else None

        synthetic_examples = []
        if marking_type == "ai" and _samples_for_synthetic_generation(full_text, mark_val):
            synthetic_result = await ai_pipeline.generate_and_validate_synthetic_answers(
                question_text=llm_context_text,
                mark_value=mark_val,
                mark_scheme=question_mark_scheme or "Award marks for correct scientific reasoning.",
                spec_code=primary_topic_code or "",
                known_misconceptions=known_misconceptions or []
            )
            synthetic_examples = synthetic_result["examples"]
            # Tag each example with its own question's number so the caller
            # can resolve it to a question_id once questions are inserted
            # (this pipeline runs before any DB insert, so no id exists yet).
            for ex in synthetic_examples:
                ex["question_number"] = q["number"]
            pipeline_results["synthetic_training_dataset"].extend(synthetic_examples)
            # New misconception tags the grader surfaced while marking a
            # synthetic answer (§6.2a feedback loop) - merged into the same
            # admin-approval queue as the document-scanning path below.
            for new_tag in synthetic_result["proposed_tags"]:
                misconceptions_from_grading.setdefault(new_tag["tag_id"], new_tag)

        # Mark-scheme worked-exemplar mining (§6.2, real-ground-truth path) -
        # unlike the synthetic generation above, this extracts answer wording
        # and marks the mark scheme itself already states rather than
        # inventing/targeting a level, so it runs for every AI-marked
        # question with scheme text available rather than being sampled for
        # cost the way invented-answer generation is.
        if marking_type == "ai" and question_mark_scheme:
            ms_exemplar_result = await ai_pipeline.generate_mark_scheme_exemplar_examples(
                question_text=llm_context_text,
                mark_value=mark_val,
                mark_scheme_text=question_mark_scheme,
                spec_code=primary_topic_code or "",
                known_misconceptions=known_misconceptions or []
            )
            for ex in ms_exemplar_result["examples"]:
                ex["question_number"] = q["number"]
            pipeline_results["synthetic_training_dataset"].extend(ms_exemplar_result["examples"])
            for new_tag in ms_exemplar_result["proposed_tags"]:
                misconceptions_from_grading.setdefault(new_tag["tag_id"], new_tag)

        pipeline_results["questions"].append({
            "question_number": q["number"],
            "mark_value": mark_val,
            "question_text": full_text,
            "marking_type": marking_type,
            "marking_dsl": dsl,
            "mark_scheme_text": question_mark_scheme if question_mark_scheme else f"Official mark scheme rubric for Q{q['number']}",
            "images": q_images,
            "table_data": q_tables if q_tables else None,
            "needs_review": any(img.get("needs_review") for img in q_images) or bool(dsl_problems),
            # Every spec_topics.spec_code this question was classified
            # under, auto-matched by the AI splitter against the paper's
            # known topic list - usually one, but a question can legitimately
            # span more than one. Empty if unclassified (left uncategorized).
            # routers/ingestion.py resolves the first into questions.spec_
            # topic_id (kept as the "primary" topic for existing single-topic
            # consumers) and inserts every one into question_topics.
            "topic_spec_codes": topic_spec_codes,
            "answer_type": answer_type,
            "answer_options": q.get("answer_options") if using_ai_split else None,
            "exemplar_eligible": exemplar_eligible,
        })

    # 7. Real exemplar answers from the examiner report (§6.2 seed data) -
    # unlike the per-question synthetic answers generated above (purely
    # AI-invented), these are answers the report itself says real candidates
    # wrote, matched back to a question number and independently blind-graded
    # the same way, so they land in training_examples with an identical
    # label shape (distinguished only via "source": "examiner_exemplar").
    # Read once here and reused below in step 8's misconception scan, rather
    # than re-decoding the PDF bytes twice.
    er_text = None
    if examiner_report_bytes:
        er_doc = fitz.open(stream=examiner_report_bytes, filetype="pdf")
        er_text = "\n\n".join([er_doc[i].get_text() for i in range(len(er_doc))])
        exemplar_result = await ai_pipeline.generate_exemplar_training_examples_from_report(
            er_text, pipeline_results["questions"], known_misconceptions or []
        )
        pipeline_results["synthetic_training_dataset"].extend(exemplar_result["examples"])
        for new_tag in exemplar_result["proposed_tags"]:
            misconceptions_from_grading.setdefault(new_tag["tag_id"], new_tag)

    # 8. Misconception Scanning (§6.2a) - scans whichever of the mark scheme
    # and examiner report are present (both optional uploads) and merges the
    # results, deduped by tag_id, so a misconception surfaced by both isn't
    # proposed twice. A mark scheme alone often reveals common wrong answers
    # via its "do not accept"/"common error" annotations even with no
    # separate examiner report uploaded.
    # Ground each scan against everything already known for this
    # specification - both what was already in the DB before this upload
    # (existing_taxonomy) and whatever grading synthetic/exemplar answers
    # just surfaced above in this same run - so the document scan doesn't
    # re-propose a misconception under a new tag_id/phrasing (§6.2a).
    proposed_by_tag: Dict[str, Dict[str, Any]] = dict(misconceptions_from_grading)
    scan_context = list(existing_taxonomy or []) + list(misconceptions_from_grading.values())
    if mark_scheme_text:
        for tag in await ai_pipeline.scan_text_for_misconceptions(mark_scheme_text, "mark scheme", known_topics or [], existing_tags=scan_context):
            proposed_by_tag[tag["tag_id"]] = tag
            scan_context.append(tag)
    if er_text:
        for tag in await ai_pipeline.scan_text_for_misconceptions(er_text, "examiner report", known_topics or [], existing_tags=scan_context):
            proposed_by_tag[tag["tag_id"]] = tag
            scan_context.append(tag)
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

_GB_TIER_SUFFIX_RE = re.compile(r'^(.*?)\s+TIER\s+([A-Z]+)$', re.IGNORECASE)

def _gb_normalize(s: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', s.upper())

def parse_grade_boundaries_table(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Deterministically parses an exam board's official grade boundaries PDF
    via its real table structure (pdfplumber), rather than flattening the
    page to text and asking an LLM to reconstruct the table -
    ai_pipeline.extract_grade_boundaries_from_text proved unreliable at
    reading large numeric tables this way and produced wrong boundaries
    (routers/qualifications.py's ingest_grade_boundaries falls back to it
    only when this function finds no usable rows, e.g. a scanned/image-only
    document with no real table layer).

    Assumes the common UK exam board layout: one row per subject (or
    subject/tier), columns [subject_code, subject_title, maximum_mark,
    <grade values...>], with a header row (first two cells blank, remaining
    cells holding the grade labels for the rows that follow - e.g. "9 8 7
    6 5 4 3 2 1", or GCSE Double Award's combined "99 98 88 ...") -
    different sections of the same document (e.g. a "GCSE" section vs a
    "GCSE Double Award" section) can use a different label set, so labels
    are read from whichever header row most recently preceded a row rather
    than assumed fixed.

    Returns a flat list of {"subject_code", "title", "max_mark",
    "boundaries": [{"grade", "raw_mark"}, ...]} - one entry per data row, in
    document order. A "-" cell (grade not applicable to that tier) is
    omitted from boundaries rather than treated as zero. Returns [] if no
    usable rows were found at all.
    """
    rows_out: List[Dict[str, Any]] = []
    current_labels: Optional[List[str]] = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    cells = [c.strip() if isinstance(c, str) else c for c in row]
                    if len(cells) < 4:
                        continue

                    if not cells[0] and not cells[1]:
                        labels = [c for c in cells[3:] if c]
                        if labels:
                            current_labels = labels
                        continue

                    subject_code, title, max_mark_cell = cells[0], cells[1], cells[2]
                    if not subject_code or not title or not current_labels:
                        continue
                    try:
                        max_mark = int(max_mark_cell)
                    except (TypeError, ValueError):
                        continue

                    boundaries = []
                    for label, value in zip(current_labels, cells[3:]):
                        if value in (None, '', '-'):
                            continue
                        try:
                            raw_mark = int(value)
                        except (TypeError, ValueError):
                            continue
                        boundaries.append({"grade": label, "raw_mark": raw_mark})

                    if boundaries:
                        rows_out.append({
                            "subject_code": subject_code,
                            "title": title,
                            "max_mark": max_mark,
                            "boundaries": boundaries,
                        })

    return rows_out

def _gb_row_code_tier_letter(row: Dict[str, Any]) -> Optional[str]:
    """Fallback tier signal when the title has no '... TIER F/H' suffix: the subject code's own trailing letter."""
    code_tail = row["subject_code"].strip()[-1:].upper()
    return code_tail if code_tail.isalpha() else None

def _gb_split_title(title: str) -> Tuple[str, Optional[str]]:
    """Splits a document title into (bare_title, tier_letter_from_suffix_or_None)."""
    m = _GB_TIER_SUFFIX_RE.match(title)
    if m:
        return m.group(1).strip(), m.group(2).strip().upper()
    return title.strip(), None

def _gb_finalize_row(
    row: Dict[str, Any],
    candidate: Dict[str, Any],
    tier_letter: Optional[str],
    seen_keys: set,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Resolves one already-subject-matched row into a (matched_entry, None) or
    (None, skipped_entry) pair - tier resolution, dedup against `seen_keys`,
    and raw-mark-to-percentage conversion, shared by both the deterministic
    containment match and the AI-assisted fallback match below so the two
    passes apply identical tier/dedup rules.
    """
    qual_tiers = candidate.get("tiers") or []
    if qual_tiers:
        if not tier_letter:
            return None, {"subject": candidate["subject"], "tier": '', "reason": "qualification is tiered but no tier could be read from this row"}
        tier_match = next((t for t in qual_tiers if t.strip().upper().startswith(tier_letter)), None)
        if not tier_match:
            return None, {"subject": candidate["subject"], "tier": tier_letter, "reason": f"tier not recognised (expected one of {qual_tiers})"}
        tier = tier_match
    else:
        tier = ''

    key = (candidate["subject"], tier)
    if key in seen_keys:
        return None, {"subject": candidate["subject"], "tier": tier, "reason": "already matched by an earlier row for this subject/tier"}

    boundaries = [
        {"grade": b["grade"], "min_pct": round(b["raw_mark"] / row["max_mark"] * 100, 1)}
        for b in row["boundaries"]
    ]
    seen_keys.add(key)
    return {"subject": candidate["subject"], "tier": tier, "boundaries": boundaries}, None

def match_grade_boundary_rows(
    rows: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Matches parse_grade_boundaries_table's rows against this app's own
    qualifications for the exam board/level being ingested (candidates =
    [{"subject": ..., "tiers": [...]}, ...]), and converts each matched
    row's raw marks into the percentages grade_boundaries actually stores.

    A document title is matched against a candidate subject by normalized
    (uppercase, punctuation-stripped) containment in either direction,
    since board wording rarely matches our subject string exactly (e.g.
    "ART & DESIGN (FINE ART)" vs "Art & Design") - the longest/most
    specific matching candidate wins. This catches most real documents, but
    is a plain substring test, so an abbreviation the containment check
    can't see through (e.g. AQA's "COMBINED SCI: TRILOGY" vs our "Combined
    Science: Trilogy") won't match here - match_unmatched_grade_boundaries
    below runs an AI-assisted second pass over whatever's left unrecognised.

    Tier is read from a "... TIER F/H" title suffix if present (AQA's
    convention); if not, it falls back to the subject code's own trailing
    letter (e.g. Edexcel-style "1BI0/1F" vs "1BI0/1H"), matched against the
    qualification's tier names by first letter. A subject with no candidate
    match, a tier that can't be resolved either way, an untiered
    qualification whose row specifies a tier (or vice versa), or a second
    row that would silently overwrite an already-matched (subject, tier)
    pair - which also guards against a later per-component/per-paper table
    in the same document clobbering the correct subject-level numbers, even
    if this document doesn't use AQA's exact "Component grade boundaries"
    section heading - are all reported back as skipped with a reason,
    rather than guessed at.

    Returns (matched, skipped): matched = [{"subject", "tier", "boundaries":
    [{"grade", "min_pct"}, ...]}, ...]; skipped = [{"subject", "tier",
    "reason"}, ...] (subject/tier here are the document's own raw values,
    for display).
    """
    norm_candidates = [(c, _gb_normalize(c["subject"])) for c in candidates if _gb_normalize(c["subject"])]
    matched: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    seen_keys: set = set()

    for row in rows:
        bare_title, suffix_tier_letter = _gb_split_title(row["title"])
        tier_letter = suffix_tier_letter if suffix_tier_letter else _gb_row_code_tier_letter(row)

        norm_bare = _gb_normalize(bare_title)
        best = None
        for cand, norm_cand in norm_candidates:
            if norm_cand and (norm_cand in norm_bare or norm_bare in norm_cand):
                if best is None or len(norm_cand) > len(_gb_normalize(best["subject"])):
                    best = cand

        if best is None:
            skipped.append({"subject": row["title"], "tier": tier_letter or '', "reason": "subject not recognised"})
            continue

        entry, skip = _gb_finalize_row(row, best, tier_letter, seen_keys)
        (matched if entry else skipped).append(entry or skip)

    return matched, skipped

async def match_unmatched_grade_boundaries(
    rows: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Second pass over whatever match_grade_boundary_rows's plain-containment
    check reported as "subject not recognised" - asks the LLM to map the
    document's own (still unmatched) subject titles onto this app's
    candidate subject names by meaning rather than substring, so an
    abbreviation like AQA's "COMBINED SCI: TRILOGY" can still resolve to a
    candidate named "Combined Science: Trilogy". Only the subject-identity
    decision goes through the LLM here - grade boundary numbers themselves
    are still the deterministic raw-mark-to-percentage conversion from
    parse_grade_boundaries_table's own data via _gb_finalize_row, exactly as
    in the first pass, so this can't introduce a wrong-number bug even if
    the LLM's subject mapping is wrong (worst case: a row lands on the
    wrong subject, or on none, not a distorted percentage).

    Returns (extra_matched, remaining_skipped) - remaining_skipped is
    `skipped` with any now-resolved "subject not recognised" entries
    removed (other skip reasons, e.g. an unresolvable tier, pass through
    unchanged since the AI pass doesn't re-derive those).
    """
    unmatched_titles = sorted({s["subject"] for s in skipped if s["reason"] == "subject not recognised"})
    if not unmatched_titles:
        return [], skipped

    bare_by_title = {title: _gb_split_title(title)[0] for title in unmatched_titles}
    candidate_names = [c["subject"] for c in candidates]

    mapping = await ai_pipeline.match_unmatched_grade_boundary_subjects(
        sorted(set(bare_by_title.values())), candidate_names
    )
    if not mapping:
        return [], skipped

    cand_by_name = {c["subject"].strip().lower(): c for c in candidates}
    rows_by_title = {}
    for row in rows:
        rows_by_title.setdefault(row["title"], []).append(row)

    extra_matched: List[Dict[str, Any]] = []
    extra_skipped: List[Dict[str, Any]] = []
    resolved_titles = set()
    seen_keys: set = set()

    for title in unmatched_titles:
        bare_title = bare_by_title[title]
        mapped_subject = mapping.get(bare_title)
        if not mapped_subject:
            continue
        candidate = cand_by_name.get(mapped_subject.strip().lower())
        if not candidate:
            continue

        for row in rows_by_title.get(title, []):
            _, suffix_tier_letter = _gb_split_title(row["title"])
            tier_letter = suffix_tier_letter if suffix_tier_letter else _gb_row_code_tier_letter(row)
            entry, skip = _gb_finalize_row(row, candidate, tier_letter, seen_keys)
            (extra_matched if entry else extra_skipped).append(entry or skip)
        resolved_titles.add(title)

    remaining_skipped = [
        s for s in skipped
        if not (s["reason"] == "subject not recognised" and s["subject"] in resolved_titles)
    ] + extra_skipped

    return extra_matched, remaining_skipped
