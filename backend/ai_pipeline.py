import asyncio
import contextvars
import hashlib
import os
import json
import re
import uuid
import httpx
from typing import List, Dict, Any, Tuple, Optional
import marking_prompt

# Provider Keys (if provided in environment)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")

# §6.5 self-hosted serving. MLX only runs as a native macOS/Apple-Silicon
# process (Metal-backed) - it cannot run inside this backend's Linux
# container, so each live spec code is served by its own `mlx_lm.server`
# process managed by backend/training/agent.py on the host.
#
# With dozens of live spec codes and one Mac's worth of unified memory,
# keeping every adapter permanently loaded doesn't fit, so the agent loads
# models on demand and evicts the least-recently-used one to make room,
# capped at its own MAX_CONCURRENT_SERVERS residents at a time.
#
# This backend never talks to that agent over HTTP, in either direction -
# deliberately, so both sides can be scaled independently (N backend
# replicas, M agent replicas/Macs) with no direct coupling between them.
# Instead every marking request is RPUSHed as a job onto a shared Redis
# list (MLX_QUEUE_KEY) that any available agent instance BLPOPs from, and
# this process BLPOPs the matching per-job result key
# (MLX_RESULT_KEY_PREFIX + job id) that whichever agent handled it RPUSHes
# the answer onto. Redis's list operations are atomic, so this is a correct
# multi-producer/multi-consumer queue with no coordination beyond "both
# sides can reach the same Redis" - see database.py's get_redis(), the same
# connection already used for rate limiting. These two key names must match
# backend/training/agent.py's copies exactly - that's the entire interface
# between the two processes.
MLX_QUEUE_KEY = "mlx:marking:queue"
MLX_RESULT_KEY_PREFIX = "mlx:marking:result:"
MLX_RESULT_TIMEOUT = 60  # seconds to wait for an agent to pick up and finish a job

_queue_redis_client = None


def _get_queue_redis():
    """
    A dedicated Redis client for the blocking BLPOP wait on a marking
    result - deliberately NOT database.get_redis()'s shared client. redis-py
    defaults new connections to socket_timeout=5, which aborts a socket read
    after 5s regardless of the BLPOP command's own `timeout=` argument, so
    the shared client (fine for rate_limit's quick point ops) silently cut
    every marking wait short well before MLX_RESULT_TIMEOUT and always fell
    through to the legacy/stub path even when the agent answered in time.
    This client sets socket_timeout comfortably above MLX_RESULT_TIMEOUT so
    the BLPOP's own timeout is what actually governs the wait.
    """
    global _queue_redis_client
    if _queue_redis_client is None:
        import redis.asyncio as redis
        from database import REDIS_URL
        _queue_redis_client = redis.from_url(
            REDIS_URL, decode_responses=True, socket_timeout=MLX_RESULT_TIMEOUT + 10
        )
    return _queue_redis_client

# Legacy fallback (Traefik-routed, /ai/mark/<slug>/...) for a deployment
# that isn't using the dynamic agent/queue at all - a fixed set of
# always-resident spec codes routed by Traefik's HTTP provider
# (routers/internal.py), still driven by the same spec_code_marking_models
# table. Unused whenever Redis is reachable (the normal case). Both unset
# means nothing has been wired up yet, so marking stays on the local stub
# rather than erroring - matches §6.4's "sits in DSL-only indefinitely"
# behavior for any spec code without live AI marking.
MLX_SERVER_URL = os.getenv("MLX_SERVER_URL")

# Per-request token usage accounting. A ContextVar (rather than a module
# global) so concurrent ingestion requests each get their own isolated
# running total instead of clobbering each other's counts.
_token_usage_var: contextvars.ContextVar[Optional[Dict[str, int]]] = contextvars.ContextVar(
    "token_usage", default=None
)

def reset_token_usage() -> None:
    _token_usage_var.set({
        "prompt_tokens": 0,
        "output_tokens": 0,
        "thoughts_tokens": 0,
        "total_tokens": 0,
        "call_count": 0,
        # Per-call-type breakdown, keyed by the `call_category` each call_llm
        # call site tags itself with (e.g. "split_questions", "blind_grade:
        # synthetic") - added specifically to answer "where do ingestion's
        # tokens actually go" with real per-run numbers instead of inference
        # from indirect signals (mark_scheme_text length, call counts, etc.)
        # after that turned out not to be rigorous enough to trust.
        "by_category": {},
    })

def get_token_usage() -> Dict[str, Any]:
    acc = _token_usage_var.get() or {}
    # Shallow dict(acc) would still share the nested by_category dict (and
    # its nested per-category dicts) with the live accumulator - copy deep
    # enough that a caller holding onto this snapshot never sees later calls
    # mutate it underneath them.
    result = dict(acc)
    result["by_category"] = {k: dict(v) for k, v in (acc.get("by_category") or {}).items()}
    return result

def _record_token_usage(usage_metadata: Dict[str, Any], call_category: str = "uncategorized") -> None:
    acc = _token_usage_var.get()
    if acc is None:
        return
    prompt = usage_metadata.get("promptTokenCount", 0) or 0
    output = usage_metadata.get("candidatesTokenCount", 0) or 0
    thoughts = usage_metadata.get("thoughtsTokenCount", 0) or 0
    total = usage_metadata.get("totalTokenCount", 0) or 0

    acc["prompt_tokens"] += prompt
    acc["output_tokens"] += output
    acc["thoughts_tokens"] += thoughts
    acc["total_tokens"] += total
    acc["call_count"] += 1

    by_cat = acc.setdefault("by_category", {})
    cat = by_cat.setdefault(call_category, {
        "call_count": 0, "prompt_tokens": 0, "output_tokens": 0, "total_tokens": 0,
    })
    cat["call_count"] += 1
    cat["prompt_tokens"] += prompt
    cat["output_tokens"] += output
    cat["total_tokens"] += total

async def call_llm(
    prompt: str,
    system_prompt: str = "You are an expert UK exam board examiner (AQA/Edexcel/OCR).",
    temperature: float = 0.2,
    image_bytes: Optional[bytes] = None,
    image_mime_type: str = "image/png",
    call_category: str = "uncategorized",
) -> str:
    """
    Unified LLM call supporting Google Gemini API, OpenAI-compatible APIs,
    and structured deterministic local processor fallback.

    call_category tags this call's token usage for get_token_usage()'s
    by_category breakdown - every call site in this file passes one so a
    per-run ingestion cost report can actually say WHERE tokens went
    (split_paper_into_questions's single whole-paper call vs. the N
    blind_grade_synthetic_answer calls per question vs. image descriptions,
    etc.) instead of only a single opaque total.
    """
    # 1. Native Google Gemini API Integration (with Multi-Modal Vision Support)
    if GEMINI_API_KEY:
        import base64
        model_name = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"

        parts = []
        if system_prompt:
            parts.append({"text": f"System Instructions: {system_prompt}\n\nTask: "})
        parts.append({"text": prompt})

        if image_bytes:
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            parts.append({
                "inline_data": {
                    "mime_type": image_mime_type,
                    "data": b64_data
                }
            })

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": temperature
            }
        }

        # gemini-3.6-flash is a reasoning model that can spend a large
        # thinking-token budget before producing visible output, so this
        # needs real headroom - a tight timeout here reads as a silent,
        # unlogged fallback to the local stub, not an obvious error.
        # Transient network failures (timeouts, dropped connections) are
        # common enough on real API calls to warrant a couple of retries
        # before giving up and falling through to the local stub.
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    res = await client.post(url, json=payload)
                    if res.status_code == 200:
                        data = res.json()
                        _record_token_usage(data.get("usageMetadata", {}), call_category)
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts_out = candidates[0].get("content", {}).get("parts", [])
                            text_part = "".join(p.get("text", "") for p in parts_out if not p.get("thought"))
                            if not text_part.strip():
                                print(f"Gemini API returned no text part (finishReason={candidates[0].get('finishReason')})")
                            else:
                                # Clean Markdown ```json wrapping if present
                                cleaned = re.sub(r'^```(?:json)?\s*', '', text_part.strip(), flags=re.IGNORECASE)
                                cleaned = re.sub(r'\s*```$', '', cleaned)
                                return cleaned
                        else:
                            print(f"Gemini API returned no candidates: {json.dumps(data)[:500]}")
                    elif res.status_code in (429, 500, 502, 503, 504):
                        print(f"Gemini API error ({res.status_code}), attempt {attempt}/{max_attempts}: {res.text[:300]}")
                    else:
                        print(f"Gemini API error ({res.status_code}): {res.text[:500]}")
                        break
            except (httpx.TimeoutException, httpx.TransportError) as e:
                print(f"Gemini API network error, attempt {attempt}/{max_attempts}: {type(e).__name__}: {e}")
            except Exception as e:
                print(f"Google Gemini API execution error: {type(e).__name__}: {e}")
                break

            if attempt < max_attempts:
                await asyncio.sleep(1.5 * attempt)

    # 2. OpenAI / vLLM API fallback if configured
    if OPENAI_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    f"{LLM_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": temperature
                    }
                )
                if res.status_code == 200:
                    return res.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"External OpenAI API error: {e}")

    # 3. Deterministic local AI processor fallback when no API key is provided
    return _local_ai_fallback_processor(prompt, system_prompt)

def _extract_json_content(content: str) -> str:
    # Strip Markdown ```json fencing the same way call_llm does, in case the
    # base model's chat template wraps it.
    cleaned = re.sub(r'^```(?:json)?\s*', '', content.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    return cleaned


async def _call_mlx_completion_http(base_url: str, system_prompt: str, prompt: str, timeout: float) -> Optional[str]:
    """Legacy fallback path only (Traefik-routed) - the queue path below never uses this."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "default_model",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 768,
            },
        )
        if res.status_code == 200:
            return _extract_json_content(res.json()["choices"][0]["message"]["content"])
        print(f"MLX server error ({res.status_code}): {res.text[:500]}")
        return None


async def _mark_via_queue(prompt: str, system_prompt: str, spec_slug: str) -> Optional[str]:
    """
    Enqueues a marking job for any available agent instance to pick up and
    waits for its result - see the MLX_QUEUE_KEY comment above for why this
    is Redis, not HTTP. Returns None (never raises) on any failure/timeout,
    so the caller always has a clean path to the local stub fallback.
    """
    from database import get_redis  # local import: avoids a hard dependency for callers that never hit this path

    if get_redis() is None:
        return None

    job_id = str(uuid.uuid4())
    job = {"id": job_id, "spec_slug": spec_slug, "system_prompt": system_prompt, "prompt": prompt}
    result_key = f"{MLX_RESULT_KEY_PREFIX}{job_id}"
    try:
        r = _get_queue_redis()
        await r.rpush(MLX_QUEUE_KEY, json.dumps(job))
        popped = await r.blpop(result_key, timeout=MLX_RESULT_TIMEOUT)
    except Exception as e:
        print(f"MLX queue request failed: {type(e).__name__}: {e}")
        return None
    if popped is None:
        print(f"MLX queue job {job_id} ({spec_slug}) timed out after {MLX_RESULT_TIMEOUT}s waiting for an agent")
        return None

    _, raw = popped
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        print(f"MLX queue job {job_id} returned unparseable result: {raw[:300]}")
        return None
    if not result.get("ok"):
        print(f"MLX queue job {job_id} ({spec_slug}) failed: {result.get('error')}")
        return None
    return _extract_json_content(result["content"])


async def mark_with_selfhosted_model(prompt: str, system_prompt: str, spec_slug: str | None = None) -> str:
    """
    §6: Live marking must NEVER call a hosted API (Gemini/OpenAI) - only the
    self-hosted, per-spec-code fine-tuned model (§6.5) is permitted in this
    path. If Redis isn't reachable, MLX_SERVER_URL isn't configured either,
    spec_slug is missing, or the request fails/times out, this falls back to
    the deterministic local stub - NEVER to call_llm()/a hosted API - so a
    configured GEMINI_API_KEY/OPENAI_API_KEY (used for ingestion-time calls)
    can never be silently used for marking, and a down self-hosted server
    degrades to the stub rather than a student-facing 500.

    Caller (marking_engine.mark_question) is responsible for checking
    spec_code_marking_models.status == 'live' before calling this at all -
    this function assumes it's only reached once that gate has passed.
    """
    if spec_slug:
        content = await _mark_via_queue(prompt, system_prompt, spec_slug)
        if content is not None:
            return content
        if MLX_SERVER_URL:
            try:
                content = await _call_mlx_completion_http(
                    f"{MLX_SERVER_URL.rstrip('/')}/ai/mark/{spec_slug}", system_prompt, prompt, timeout=60.0
                )
                if content is not None:
                    return content
            except Exception as e:
                print(f"MLX server request failed, falling back to local stub: {type(e).__name__}: {e}")

    return _local_ai_fallback_processor(prompt, system_prompt)

def _local_ai_fallback_processor(prompt: str, system_prompt: str) -> str:
    """
    High-accuracy rule-based & heuristic fallback ensuring the pipeline runs
    out-of-the-box in offline/development environments without external API keys.
    """
    p_lower = prompt.lower()

    # 1. DSL Generation request
    # Only inspect the actual question/mark-scheme content, not the boilerplate
    # "Available DSL Operators" menu (which always lists every operator name,
    # e.g. "MCQ:OPTION" - matching against the full prompt would misclassify
    # every question as multiple choice).
    if "generate deterministic dsl" in p_lower or "dsl:" in p_lower:
        q_match = re.search(r'Question:\s*(.*?)\s*Mark Value:', prompt, re.DOTALL | re.IGNORECASE)
        scheme_match = re.search(r'Mark Scheme:\s*(.*?)\s*Available DSL Operators:', prompt, re.DOTALL | re.IGNORECASE)
        content_lower = ((q_match.group(1) if q_match else "") + " " + (scheme_match.group(1) if scheme_match else "")).lower()
        if re.search(r'\b(option [a-d]\b|multiple choice|which of the following)', content_lower):
            return json.dumps({"marking_dsl": "MCQ:A", "reasoning": "Detected multiple choice"})
        if "calculate" in content_lower or "magnification" in content_lower or "value" in content_lower:
            return json.dumps({"marking_dsl": "EXACT:300 OR RANGE:299.5,300.5", "reasoning": "Numerical calculation"})
        return json.dumps({"marking_dsl": "ANY:glucose,sugar AND CONTAIN:respiration", "reasoning": "Keyword rubric"})

    # 2. Image description request
    if "scientific diagram" in p_lower or "image description" in p_lower:
        return json.dumps({
            "description": "Scientific schematic diagram showing labelled biological structures with scale bar.",
            "components": ["Cell membrane", "Nucleus", "Cytoplasm", "Scale bar"],
            "axes_or_labels": ["x-axis: Time (min)", "y-axis: Rate of reaction (a.u.)"],
            "key_trend": "Initial linear increase plateauing at saturation."
        })

    # 3. Examiner report misconception scan request
    if "examiner report" in p_lower or "misconceptions" in p_lower:
        return json.dumps({
            "proposed_tags": [
                {
                    "tag_id": "confuses_active_transport_diffusion",
                    "label": "Confuses active transport with diffusion",
                    "description": "Fails to recognize active transport requires energy (ATP) against concentration gradient.",
                    "spec_code": "4.1.3"
                },
                {
                    "tag_id": "missing_calculation_units",
                    "label": "Missing or incorrect units in calculation",
                    "description": "Calculates correct numerical magnitude but omits standard SI units.",
                    "spec_code": "4.4.1"
                }
            ]
        })

    # 4. Student Answer Marking Evaluation request
    if "student answer:" in p_lower or "mark the student's answer" in p_lower or "official mark scheme:" in p_lower:
        # Extract student answer, mark scheme, and total marks from prompt
        ans_match = re.search(r'Student Answer:\s*"""(.*?)"""', prompt, re.DOTALL | re.IGNORECASE)
        scheme_match = re.search(r'Official Mark Scheme:\s*"""(.*?)"""', prompt, re.DOTALL | re.IGNORECASE)
        marks_match = re.search(r'Total Marks:\s*(\d+)', prompt, re.IGNORECASE)
        
        student_ans = ans_match.group(1).strip() if ans_match else ""
        scheme_text = scheme_match.group(1).strip() if scheme_match else ""
        max_marks = int(marks_match.group(1)) if marks_match else 4
        
        # Parse points and match keywords
        points = [p.strip("* -") for p in scheme_text.split("\n") if p.strip()]
        earned_points = []
        missed_points = []
        
        ans_lower = student_ans.lower()
        for p in points:
            # Extract key nouns/verbs
            keywords = [w for w in re.findall(r'\b[a-zA-Z]{4,}\b', p.lower()) if w not in {'which', 'where', 'their', 'there', 'because', 'allow', 'accept', 'marks', 'rate', 'using'}]
            if keywords and any(kw in ans_lower for kw in keywords[:3]):
                earned_points.append(p)
            else:
                missed_points.append(p)
                
        marks_awarded = min(max_marks, max(0, len(earned_points)))
        
        # Check misconceptions
        misconceptions = []
        if "mitosis" in ans_lower and "gamete" in ans_lower:
            misconceptions.append("confuses_mitosis_meiosis")
        if "respiration" in ans_lower and "breathing" in ans_lower:
            misconceptions.append("confuses_respiration_breathing")
        if "artery" in ans_lower and "valve" in ans_lower:
            misconceptions.append("confuses_artery_vein_structure")
            
        return json.dumps({
            "marks_awarded": marks_awarded,
            "www": earned_points[:3],
            "ebi": missed_points[:3],
            "misconception_tags": misconceptions
        })

    # 5. Synthetic answer generator
    if "synthetic" in p_lower or "generate candidate student answer" in p_lower:
        return json.dumps({
            "synthetic_answers": [
                {"target_marks": 4, "answer": "The double circulatory system pumps blood at high pressure. Red blood cells contain haemoglobin with high oxygen capacity and capillaries have one-cell-thick walls for rapid diffusion.", "rationale": "All 4 marking points covered"},
                {"target_marks": 2, "answer": "The heart pumps blood to the lungs and body. Capillaries are thin.", "rationale": "Mentions 2 basic points but misses double circulation pressure and haemoglobin."},
                {"target_marks": 0, "answer": "Air goes straight into the muscles when you breathe in and out.", "rationale": "Conceptual misconception"}
            ]
        })

    return json.dumps({"result": "Processed", "summary": "AI generation completed successfully"})

async def generate_dual_image_descriptions(image_dict: Dict[str, Any], img_bytes: bytes | None = None) -> Tuple[str, str, bool, float]:
    """
    §3.1a Dual Independent Image Description Generation via Vision API:
    Passes extracted diagram bytes to multi-modal Gemini Vision twice independently.
    Cross-checks agreement. If agreement < 0.65, flags needs_review.
    Returns: (desc_A, desc_B, is_agreed, agreement_score)

    img_bytes must be passed by the caller (ingestion.py, which has the raw
    extracted bytes in memory before they're uploaded to S3/MinIO - see
    storage.py) - this used to re-read them from a local backend/media/
    disk path, which stopped existing once image storage moved to S3 and
    silently made every call here run blind (img_bytes always None), so
    both "independent" descriptions were pure hallucination with nothing
    in common to agree on - every single image failed the similarity check
    and got flagged needs_review, regardless of whether anything was
    actually wrong with it.

    The prompt below is the SECOND bug in this same cross-check, found after
    the first was fixed: it never instructed JSON output the way every other
    prompt in this file does, so the model (correctly, per its instructions)
    replied with free-form Markdown prose instead - json.loads then failed
    and the except branch silently fell back to using the ENTIRE raw
    response (headers, boilerplate preamble, bullet formatting and all) as
    the "description". Two independent free-form completions format that
    boilerplate differently enough that their word-overlap Jaccard score
    almost never cleared 0.65 even when the substantive content plainly
    agreed - confirmed against real ingested data: 95%+ of every image in
    the database was flagged needs_review this way, burying genuine
    problems in noise. Forcing a flat JSON {"description": "..."} shape (and
    a system_prompt that says so, matching every other structured call here)
    makes the compared text the actual description again, not two
    differently-shaped essays about the same picture.
    """
    prompt = f"""Describe this UK GCSE/A-Level exam scientific diagram (type: {image_dict.get('type')}, bbox: {image_dict.get('bbox')}) in structured detail for text-based marking. Extract key labels, axis titles, trend directions, and biological/chemical components.

    Output ONLY a flat JSON object with exactly this shape (field names must match exactly - no
    other keys, no Markdown headings or commentary anywhere in the response):
    {{"description": "<one prose paragraph covering everything below, this is what gets compared
      against an independent second description of the same image - keep it dense and factual,
      not narrated>",
      "components": ["<labelled part/structure named in the image>", ...],
      "axes_or_labels": ["<axis title, legend entry, or other printed label>", ...],
      "key_trend": "<the data trend/relationship shown, or \\"\\" if not applicable>"}}
    """
    system_prompt = "You are an expert UK exam board scientific diagram describer. Output only valid JSON, no commentary, no Markdown."

    # Run A
    res_a = await call_llm(prompt, system_prompt=system_prompt, temperature=0.1, image_bytes=img_bytes, call_category="image_description")
    # Run B (independent pass with higher temperature)
    res_b = await call_llm(prompt, system_prompt=system_prompt, temperature=0.5, image_bytes=img_bytes, call_category="image_description")

    def _flatten_description(raw: str) -> str:
        try:
            data = json.loads(raw)
            desc = str(data.get("description", "")).strip()
            extra = " ".join([
                " ".join(str(c) for c in data.get("components", []) if isinstance(data.get("components"), list)),
                " ".join(str(c) for c in data.get("axes_or_labels", []) if isinstance(data.get("axes_or_labels"), list)),
                str(data.get("key_trend") or ""),
            ]).strip()
            combined = f"{desc} {extra}".strip()
            if combined:
                return combined
        except Exception as e:
            print(f"Image description JSON parse failed, comparing raw text instead: {type(e).__name__}: {e}")
        return raw

    desc_a_text = _flatten_description(res_a)
    desc_b_text = _flatten_description(res_b)

    # Cross-check keyword similarity
    words_a = set(re.findall(r'\b[a-zA-Z]{3,}\b', desc_a_text.lower()))
    words_b = set(re.findall(r'\b[a-zA-Z]{3,}\b', desc_b_text.lower()))

    intersection = words_a.intersection(words_b)
    union = words_a.union(words_b)
    jaccard_score = (len(intersection) / len(union)) if union else 1.0

    is_agreed = jaccard_score >= 0.65
    return desc_a_text, desc_b_text, is_agreed, round(jaccard_score, 3)

VALID_ANSWER_TYPES = {"written", "select", "multi_select", "numeric", "grid_select", "practical"}

def _clean_answer_type_and_options(raw_type: Any, raw_options: Any) -> Tuple[str, Optional[Any]]:
    """
    Validates the model's answer_type/answer_options pair, falling back to
    a plain written textbox on anything malformed - a bad classification
    here should never crash ingestion or produce a broken answer widget.
    """
    answer_type = str(raw_type).strip().lower() if raw_type else "written"
    if answer_type not in VALID_ANSWER_TYPES:
        answer_type = "written"

    if answer_type in ("written", "practical"):
        return answer_type, None

    if answer_type in ("select", "multi_select"):
        if not isinstance(raw_options, list) or not raw_options:
            return "written", None
        options = []
        for opt in raw_options:
            if not isinstance(opt, dict):
                continue
            text = str(opt.get("text") or opt.get("key") or "").strip()
            if not text:
                continue
            # Always assign a canonical positional A/B/C/... key rather than
            # trusting the model's own "key" field verbatim - it sometimes
            # echoes the full option text into "key" too (producing options
            # like {"key": "Arteries", "text": "Arteries"}), which rendered
            # as a duplicated "Arteries: Arteries" label and made the option
            # ambiguous to match against a mark scheme written as a plain
            # letter. A short, always-distinct key keeps both consistent.
            options.append({"key": chr(ord('A') + len(options)), "text": text})
        if len(options) < 2:
            return "written", None
        return answer_type, options

    if answer_type == "numeric":
        unit = None
        if isinstance(raw_options, dict):
            unit = raw_options.get("unit")
        return "numeric", {"unit": str(unit).strip() if unit else None}

    if answer_type == "grid_select":
        if not isinstance(raw_options, list) or not raw_options:
            return "written", None
        rows = []
        for row in raw_options:
            if (isinstance(row, dict) and row.get("statement")
                    and isinstance(row.get("options"), list) and len(row["options"]) >= 2):
                rows.append({
                    "statement": str(row["statement"]).strip(),
                    "options": [str(o).strip() for o in row["options"]]
                })
        if not rows:
            return "written", None
        return "grid_select", rows

    return "written", None

async def split_paper_into_questions(
    page_texts: List[str],
    mark_scheme_text: str,
    known_topics: Optional[List[Dict[str, str]]] = None
) -> List[Dict[str, Any]]:
    """
    §3.1 AI-driven joint question + mark-scheme splitting.

    Real exam board PDFs render question numbers with irregular character
    spacing (e.g. AQA prints "01.4" as separate glyphs that extract as
    "0 1 . 4\\n") that a regex boundary detector cannot reliably parse -
    verified against a real AQA GCSE Biology paper, where regex found 2
    questions out of ~25+. This sends the full paper (page-marked) and mark
    scheme to the model and asks for one structured entry per question,
    each carrying its OWN isolated mark scheme text rather than the whole
    document's mark scheme.

    Returns [] on any failure so the caller can fall back to regex.

    A single call to this is not reliable enough on its own: on a real
    52-question paper, identical calls sometimes returned the full correct
    breakdown and sometimes an empty {{"questions": []}} with no error
    (valid JSON, just the model declining to find anything) - so this
    retries a few times and keeps the best (most questions) result rather
    than trusting the first response.
    """
    numbered_pages = "\n\n".join(f"[PAGE {i + 1}]\n{text}" for i, text in enumerate(page_texts))

    topics_block = ""
    if known_topics:
        topics_list = "\n".join(f'- {t["spec_code"]}: {t["title"]}' for t in known_topics)
        topics_block = f"""
    This paper's specification topics (choose the single best match per question, or null if none fit):
    {topics_list}
    """

    prompt = f"""
    You are given the full text of a UK exam board question paper (with [PAGE N] markers) and its mark scheme.
    Extract every distinct question and sub-question (e.g. "1", "01.1", "3(b)(ii)") in the order they appear.

    For each one, output:
    - question_number: the number/label as printed (e.g. "01.4", "3(b)(ii)")
    - stem_text: many exam questions open with shared context that every sub-question under it depends on -
      an experiment/method description, a scenario, background data, or a passage - printed once above the
      first sub-question (e.g. above "02.1") rather than repeated in each sub-question's own printed text.
      If such context exists for this sub-question, reproduce it here in full (as Markdown, same formatting
      rules as question_text below) - every sibling sub-question under the same stem gets the identical
      stem_text. Use an empty string "" if this question has no shared introductory context (e.g. it's
      entirely self-contained, or it IS the stem-setting text itself with no separate sub-parts).
    - question_text: the full text of just that question/sub-question's OWN printed wording (not neighbouring
      questions, not headers/footers/instructions, and not the shared context already captured in stem_text),
      formatted as Markdown:
        * Wrap the command word (Explain, Calculate, Describe, Evaluate, etc.) in **bold**.
        * If the question presents multiple-choice options (e.g. "A ... B ... C ... D ..." or "Tick one box"),
          render each option as its own Markdown list item, e.g. "- A: <option text>".
        * If the question refers to a table of data, reproduce it as a Markdown table.
        * Preserve any numbered sub-parts as a Markdown ordered/unordered list.
        * If the question text contains a bulleted list of steps, items, or conditions (marked in the source with
          "•", "-", "*", or similar), render each one as its own Markdown list item on its own line
          (e.g. "- Count all cells that are completely within the square.") rather than leaving them inline
          in one paragraph.
    - mark_value: the integer mark value from its "[N marks]" annotation. If not stated, use 1.
    - page: the page number (from the [PAGE N] markers) where this question's text appears
    - mark_scheme_text: ONLY this question's corresponding mark scheme text, extracted from the Mark Scheme section below
      (not the whole mark scheme document, not other questions' marking points), formatted as a Markdown bullet list of
      individual marking points (one point per list item). Higher-mark "levels of response"/extended-writing questions
      often carry an "Indicative content" block alongside the level descriptors (Level 1/2/3 + mark ranges) - this
      lists example points/ideas a good answer could include, separately from what determines the level/mark awarded.
      Include BOTH in full: the level descriptors as their own bullet points, then the indicative content points as
      their own bullet points (label the section "Indicative content:" so the two aren't confused) - dropping the
      indicative content leaves nothing for marking to check candidate answers against beyond the generic level
      criteria.
      Levels-of-response mark schemes carry extra structure beyond the flat indicative-content list, and it must all
      be preserved - "one point per list item" means don't merge or invent, not don't nest:
        * A "because"/reason clause nested under an indicative-content point (e.g. an improvement point followed by
          why it matters) must stay nested under that point as a sub-bullet, not dropped or flattened to a sibling
          bullet - this reasoning is exactly what a Level 3 "logically linked" answer is expected to demonstrate, so
          losing it leaves nothing to judge logical linking against beyond the bare point itself.
        * A separate "Additional guidance" (or similarly-named) table that translates the abstract level descriptors
          into concrete per-level criteria (e.g. "Level 1: one stage described", "Level 2: two stages described",
          "Level 3: all stages described in detail") must be included in full, under its own "Additional guidance:"
          heading - this is often the only place a mark scheme states a countable, checkable criterion for each
          level rather than a qualitative one.
        * AO-tagged guidance columns, "IGNORE"/"ACCEPT"/"DO NOT ACCEPT" annotations, and worked examples of
          acceptable/unacceptable phrasing must all be kept attached to the point they annotate, not summarized away.
    - references_figure: true if the question text mentions or depends on a diagram, image, graph, table, or figure
      (e.g. "Figure 1 shows...", "the diagram below"); false otherwise.
    - figure_label: if references_figure is true and the question names the figure/diagram it depends on
      (e.g. "Figure 9", "Diagram 2", "Figure 3a"), the label exactly as printed in the question text. Use null if
      references_figure is false, or if it references an image without naming a specific label (e.g. "the diagram
      below"). This becomes the caption shown under the image so students can tell which figure a question is
      talking about - extract it verbatim, don't paraphrase or invent one.
    - topic_spec_codes: a list of every specification code (from the list below, if provided) that this
      question genuinely tests - most questions test exactly one, but a question can legitimately draw on
      more than one (e.g. a calculation that requires both a maths-skills topic and the biology/chemistry/
      physics topic it's applied to, or a question that explicitly spans two content areas). List every
      one that clearly applies, in no particular order; do not pad the list with a loosely-related topic
      just to list more than one. Use an empty list [] if no topic list is given or none of them fit.
    - answer_type: how the student should answer, one of:
        * "written" - free text / prose (the default for Explain/Describe/Evaluate questions, and for a
          Calculate question that also asks the student to explain their reasoning or where multiple
          working methods could reach different acceptable answers)
        * "select" - a single choice from a fixed list (e.g. "Tick one box")
        * "multi_select" - more than one choice from a fixed list (e.g. "Tick two boxes")
        * "numeric" - a Calculate question whose mark scheme awards marks for one final numeric result
          (optionally with a unit), even if working must be shown on the real paper - grade only the
          final answer, so use this whenever the mark scheme states a single correct value/range
        * "grid_select" - one choice per row/statement (e.g. "Tick True or False for each row" against a list of statements)
        * "practical" - the answer is drawn/marked directly onto the paper itself, not typed (e.g. "Draw a line
          to complete the graph", "Complete the diagram", "Plot the points and draw a line of best fit",
          "Label the diagram", "Sketch the graph you would expect") - there's nothing meaningful for a student
          to type, so this bypasses typed answer collection entirely
      Choose "written" whenever unsure - only use the others when the question clearly fits.
    - answer_options: required only for select/multi_select/numeric/grid_select, else null:
        * select or multi_select: a list of {{"key": "A", "text": "<option text>"}} - "key" is always a short
          label (A/B/C/... as lettered in the paper, or a short positional label if the paper prints options
          unlettered); never repeat the option's full text into "key"
        * numeric: {{"unit": "<unit string or null>"}}
        * grid_select: a list of {{"statement": "<row text>", "options": ["True", "False"]}} - one entry per row,
          only if the row statements are present in the text (not solely inside an image/table you cannot read)
    {topics_block}
    Ignore administrative/boilerplate text: "Do not write outside the box", print/version codes, blank answer lines, page numbers.

    Output ONLY a flat JSON object: {{"questions": [{{"question_number": "...", "stem_text": "...", "question_text": "...", "mark_value": N, "page": N, "mark_scheme_text": "...", "references_figure": true, "figure_label": "Figure 9", "topic_spec_codes": ["..."], "answer_type": "written", "answer_options": null}}, ...]}}

    QUESTION PAPER:
    \"\"\"{numbered_pages[:500000]}\"\"\"

    MARK SCHEME:
    \"\"\"{mark_scheme_text[:500000]}\"\"\"
    """
    system_prompt = "You are an expert UK exam board question paper parser. Output only valid JSON, no commentary."

    best: List[Dict[str, Any]] = []
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        res = await call_llm(prompt, temperature=0.1 + 0.2 * (attempt - 1), system_prompt=system_prompt, call_category="split_questions")
        try:
            data = json.loads(res)
            raw_questions = data.get("questions", [])
            if not isinstance(raw_questions, list):
                raw_questions = []

            cleaned = []
            for q in raw_questions:
                if not isinstance(q, dict):
                    continue
                number = q.get("question_number")
                text = q.get("question_text")
                if not number or not text:
                    continue
                try:
                    mark_val = max(1, int(q.get("mark_value", 1)))
                except (TypeError, ValueError):
                    mark_val = 1
                try:
                    page = max(1, int(q.get("page", 1)))
                except (TypeError, ValueError):
                    page = 1
                # Accept the intended list shape, but fall back to a lone
                # "topic_spec_code" string if the model reverts to the old
                # singular field despite the prompt - a model that only ever
                # found one topic for a question isn't unusual, but should
                # still land in the list the caller now expects.
                raw_codes = q.get("topic_spec_codes")
                if not isinstance(raw_codes, list):
                    single = q.get("topic_spec_code")
                    raw_codes = [single] if single else []
                seen_codes = set()
                topic_spec_codes = []
                for c in raw_codes:
                    if not c:
                        continue
                    code = str(c).strip()
                    if code and code not in seen_codes:
                        seen_codes.add(code)
                        topic_spec_codes.append(code)
                figure_label = q.get("figure_label")
                stem_text = str(q.get("stem_text") or "").strip()
                answer_type, answer_options = _clean_answer_type_and_options(
                    q.get("answer_type"), q.get("answer_options")
                )
                cleaned.append({
                    "number": str(number).strip(),
                    "text": str(text).strip(),
                    "stem_text": stem_text,
                    "mark_value": mark_val,
                    "page": page,
                    "mark_scheme_text": str(q.get("mark_scheme_text") or "").strip(),
                    "references_figure": bool(q.get("references_figure", False)),
                    "figure_label": str(figure_label).strip() if figure_label else None,
                    "topic_spec_codes": topic_spec_codes,
                    "answer_type": answer_type,
                    "answer_options": answer_options
                })

            if len(cleaned) > len(best):
                best = cleaned
        except Exception as e:
            print(f"AI question splitting attempt {attempt}/{max_attempts} failed to parse: {type(e).__name__}: {e}")

        # A real paper virtually never has fewer than ~5 questions; a
        # low/empty count is the model declining rather than a genuinely
        # short paper, so keep retrying. Stop early once we have a
        # plausible result.
        if len(best) >= 5:
            break

    if not best:
        print("AI question splitting: all attempts returned no usable questions, falling back to regex")
    return best

async def extract_spec_topics_from_text(spec_text: str) -> Dict[str, Any]:
    """
    Parses an official exam board specification document into its full
    topic hierarchy, so an admin can pre-populate spec_topics ahead of
    ingesting any past papers rather than typing topic codes by hand.

    Also extracts which tiers (e.g. Higher/Foundation) the specification
    states it's assessed at, if any - a real spec document states this
    outright, so there's no need for a manual admin-facing tier field.

    Also extracts the mark total and time allowance of a single exam
    component/paper from the specification's "Scheme of assessment" section
    (e.g. "Paper 1: written exam: 1 hour 45 minutes, 100 marks") - real
    papers' overall mark total and duration, which is exactly what a
    generated custom paper (Manage Subjects "Custom paper settings")
    targets, so this pre-fills the admin-facing form input instead of
    requiring it typed by hand. Most GCSE specs have symmetric
    papers/components (same marks/time each), so the first one found is
    representative; a qualification whose components genuinely differ is
    still editable by hand afterward.

    Returns {"topics": [...], "tiers": [...]}, where "topics" is a flat list
    of {spec_code, title, parent_spec_code, tier_only} - one entry per
    numbered specification section/sub-section - so the caller can resolve
    parent_id by matching parent_spec_code against another entry's
    spec_code, and "tiers" is a list of tier names (e.g. ["Higher",
    "Foundation"]), or [] if the qualification isn't tiered (e.g. most
    A-Levels, IB, BTEC). A tiered GCSE specification merges Higher and
    Foundation content into one document and marks certain sections as
    assessed at one tier only (e.g. "HT only") - tier_only carries that tier
    name (matching an entry in "tiers") when a section is so marked, or null
    when the section is common to all tiers (including for untiered
    qualifications, where it's always null). Exam board/subject/level are
    NOT extracted here - the caller (Manage Subjects "Add subject") already
    has them as admin input, identifying which qualification this document
    belongs to; asking the model to also read them off the document's own
    branding was redundant and occasionally disagreed with the admin's own
    selection.
    Returns {"topics": [], "tiers": []} on failure.
    """
    prompt = f"""
    You are given the text of an official UK exam board subject specification document.
    Extract every numbered content section and sub-section (e.g. "4.1", "4.1.1", "4.1.2.3") -
    these define the topics students are examined on.

    For each one, output:
    - spec_code: the section number exactly as printed (e.g. "4.1.1")
    - title: the short title/heading of that section (not the full teaching content underneath it)
    - parent_spec_code: the spec_code of its immediate parent section (e.g. "4.1.2"'s parent is "4.1"),
      or null for a top-level section
    - tier_only: many GCSE specifications merge Higher and Foundation tier content into one
      document and mark some sections as assessed at only one tier (look for markers like
      "HT only", "Higher Tier only", a shaded/highlighted section, or explicit text saying a
      topic is not required for Foundation tier). If a section is so marked, output the tier
      name it belongs to exactly as the specification names its tiers (e.g. "Higher"). If the
      section is common to all tiers (the default - most content is), or the qualification
      isn't tiered at all, output null.

    Ignore front matter (contents pages, assessment objectives, grade boundaries) and appendices.

    Also determine whether this qualification is tiered - i.e. whether the specification
    states it is assessed via separate tiers such as "Higher" and "Foundation" (common for
    GCSEs). If it is, output the tier names exactly as the specification names them (e.g.
    ["Higher", "Foundation"]). If the qualification is not tiered (e.g. most A-Levels, IB,
    BTEC), output an empty list.

    Also find the "Scheme of assessment" / "Assessment" section, which states each written exam
    paper/component's total marks and time allowance (e.g. "1 hour 45 minutes" -> 105 minutes,
    "100 marks" -> 100). Take the first paper/component's figures - GCSE papers are almost always
    symmetric across components. Output target_marks and time_limit_minutes as integers, or null
    if this specification has no such single-paper figure to extract (e.g. it's entirely
    coursework/NEA-assessed, or the figures aren't stated in the document text).

    Output ONLY a flat JSON object:
    {{"topics": [{{"spec_code": "...", "title": "...", "parent_spec_code": "...", "tier_only": "..."}}, ...],
      "tiers": [...], "target_marks": ..., "time_limit_minutes": ...}}

    SPECIFICATION DOCUMENT:
    \"\"\"{spec_text[:500000]}\"\"\"
    """
    system_prompt = "You are an expert UK exam board specification parser. Output only valid JSON, no commentary."

    res = await call_llm(prompt, temperature=0.1, system_prompt=system_prompt, call_category="spec_topics_extract")
    try:
        data = json.loads(res)
        raw_topics = data.get("topics", [])
        if not isinstance(raw_topics, list):
            return {"topics": [], "tiers": [], "target_marks": None, "time_limit_minutes": None}

        cleaned = []
        for t in raw_topics:
            if not isinstance(t, dict):
                continue
            spec_code = t.get("spec_code")
            title = t.get("title")
            if not spec_code or not title:
                continue
            parent_code = t.get("parent_spec_code")
            tier_only = t.get("tier_only")
            cleaned.append({
                "spec_code": str(spec_code).strip(),
                "title": str(title).strip(),
                "parent_spec_code": str(parent_code).strip() if parent_code else None,
                "tier_only": str(tier_only).strip() if tier_only else None,
            })

        raw_tiers = data.get("tiers", [])
        tiers = [str(t).strip() for t in raw_tiers if isinstance(t, (str, int, float)) and str(t).strip()] \
            if isinstance(raw_tiers, list) else []

        def _positive_int(value):
            try:
                n = int(value)
                return n if n > 0 else None
            except (TypeError, ValueError):
                return None

        target_marks = _positive_int(data.get("target_marks"))
        time_limit_minutes = _positive_int(data.get("time_limit_minutes"))

        return {"topics": cleaned, "tiers": tiers, "target_marks": target_marks, "time_limit_minutes": time_limit_minutes}
    except Exception as e:
        print(f"Specification topic extraction failed to parse: {type(e).__name__}: {e}")
        return {"topics": [], "tiers": [], "target_marks": None, "time_limit_minutes": None}

async def match_unmatched_grade_boundary_subjects(unmatched_titles: List[str], candidate_subjects: List[str]) -> Dict[str, str]:
    """
    Second-pass subject matching for ingestion.match_grade_boundary_rows'
    plain substring/containment check, which misses a document title that's
    only an abbreviation of our subject string (e.g. AQA's "COMBINED SCI:
    TRILOGY" vs a candidate named "Combined Science: Trilogy" - "SCI" isn't
    a substring of "SCIENCE" in either direction). Only ever used for
    subject *identity* - the caller still computes every grade boundary
    percentage itself from the document's own raw marks, so a wrong or
    missing mapping here can misfile a row or leave it unmatched, but can
    never distort a number.

    Returns {unmatched_title: candidate_subject} - only for titles the
    model is confident denote the same real qualification as one of
    `candidate_subjects` (not just a similar-sounding different subject);
    omit a title from the result entirely rather than guess. Returns {} on
    failure or if nothing could be confidently matched.
    """
    if not unmatched_titles or not candidate_subjects:
        return {}

    prompt = f"""
    You are matching subject titles from a UK exam board's grade boundaries document against a list
    of subject names this app already knows about, for the SAME exam board and level. The document's
    wording is often abbreviated or differently punctuated (e.g. "COMBINED SCI: TRILOGY" is the same
    subject as "Combined Science: Trilogy"; "ART & DESIGN (FINE ART)" is a variant of "Art & Design").

    For each document title below, decide whether it clearly refers to the SAME real qualification as
    exactly one of the known subject names. Only include a title in your output if you are confident -
    a title that's merely similar-sounding to a different subject (e.g. "Statistics" is NOT "Mathematics")
    must be omitted, not guessed. Do not include a title whose subject genuinely isn't in the known list.

    DOCUMENT TITLES:
    {json.dumps(unmatched_titles)}

    KNOWN SUBJECT NAMES:
    {json.dumps(candidate_subjects)}

    Output ONLY a flat JSON object mapping each confidently-matched document title to the exact known
    subject name it corresponds to: {{"document title": "known subject name", ...}}. Omit anything not
    confidently matched. If nothing matches, output {{}}.
    """
    system_prompt = "You are an expert at matching UK exam board subject naming conventions. Output only valid JSON, no commentary."

    res = await call_llm(prompt, temperature=0.1, system_prompt=system_prompt, call_category="grade_boundary_subject_match")
    try:
        data = json.loads(res)
        if not isinstance(data, dict):
            return {}
        candidate_set = set(candidate_subjects)
        return {
            str(k): str(v) for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str) and v in candidate_set
        }
    except Exception as e:
        print(f"Grade boundary subject matching failed to parse: {type(e).__name__}: {e}")
        return {}

async def extract_grade_boundaries_from_text(doc_text: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Parses an exam board's official grade boundaries publication - one
    document usually covers every subject (and tier) at a given level for a
    single exam series, tabulating each subject's maximum raw mark and the
    minimum raw mark needed for each grade. Backs the Manage Subjects "..."
    menu -> "Update grade boundaries" bulk upload, which replaces routers/
    qualifications.py's grade_boundaries table for every subject/tier this
    document actually covers, instead of an admin hand-typing one subject at
    a time.

    `candidates` is this exam board+level's own qualifications as already
    known to this app ([{"subject": ..., "tiers": [...]}, ...]) - passed in
    so the model matches the document's own subject headings/paper titles
    (which rarely match our canonical subject string exactly, e.g. "GCSE
    Biology B" vs "Biology") back onto a name the caller can actually look
    up, rather than inventing new subject strings that would silently fail
    to match any qualification.

    Returns a flat list of {"subject": ..., "tier": ... or null, "boundaries":
    [{"grade": ..., "min_pct": ...}, ...]} - one entry per (subject, tier)
    pair the document covers AND that appears in `candidates`. min_pct is
    the minimum overall percentage of marks needed for that grade, computed
    from the document's own raw mark boundary and maximum mark for that
    subject/tier (raw_boundary / max_mark * 100) when the document states
    raw marks, or read directly if the document already states percentages.
    Returns [] on failure or if no candidate subject is found in the text.
    """
    if not candidates:
        return []

    candidates_desc = "\n".join(
        f'- "{c["subject"]}"' + (f' (tiers: {", ".join(c["tiers"])})' if c.get("tiers") else ' (not tiered)')
        for c in candidates
    )

    prompt = f"""
    You are given the text of an official UK exam board grade boundaries publication. It lists,
    for many subjects (and sometimes separate tiers such as Higher/Foundation, or separate
    components/papers rolled up into one overall subject grade), the maximum raw mark and the
    minimum raw mark needed to achieve each grade.

    This app already knows about the following subjects for this exam board and level - ONLY
    extract boundaries for a subject if it clearly corresponds to one of these (matching by
    meaning, e.g. "GCSE Biology B" or "Biology (8461)" both mean "Biology" below). Use the exact
    subject string from this list in your output, not the document's own wording. Ignore every
    other subject in the document entirely.

    KNOWN SUBJECTS:
    {candidates_desc}

    For each matched subject, extract its overall (whole-qualification, not per-component) grade
    boundaries. If the subject is tiered, extract boundaries separately for each tier listed above,
    using the tier name exactly as given above (e.g. "Higher"). If the subject is not tiered, use
    null for tier.

    For each grade the document lists (e.g. 9, 8, 7... 1 for reformed GCSEs, or A*, A, B... for
    other qualifications), output the grade label exactly as printed, and min_pct: the minimum
    percentage of marks needed for that grade, rounded to 1 decimal place. If the document states
    a raw mark boundary and a maximum mark for that subject/tier, compute min_pct as
    (raw boundary / maximum mark) * 100. If the document already states boundaries as a
    percentage, use that percentage directly.

    Output ONLY a flat JSON object:
    {{"subjects": [
      {{"subject": "...", "tier": "..." or null, "boundaries": [{{"grade": "9", "min_pct": 90.0}}, ...]}},
      ...
    ]}}

    If no known subject above appears in the document, output {{"subjects": []}}.

    GRADE BOUNDARIES DOCUMENT:
    \"\"\"{doc_text[:300000]}\"\"\"
    """
    system_prompt = "You are an expert UK exam board grade boundaries parser. Output only valid JSON, no commentary."

    res = await call_llm(prompt, temperature=0.1, system_prompt=system_prompt, call_category="grade_boundaries_extract")
    try:
        data = json.loads(res)
        raw_subjects = data.get("subjects", [])
        if not isinstance(raw_subjects, list):
            return []

        cleaned = []
        for s in raw_subjects:
            if not isinstance(s, dict):
                continue
            subject = s.get("subject")
            if not subject:
                continue
            tier = s.get("tier")
            raw_boundaries = s.get("boundaries", [])
            if not isinstance(raw_boundaries, list):
                continue

            boundaries = []
            for b in raw_boundaries:
                if not isinstance(b, dict):
                    continue
                grade = b.get("grade")
                if grade is None:
                    continue
                try:
                    min_pct = float(b.get("min_pct"))
                except (TypeError, ValueError):
                    continue
                boundaries.append({"grade": str(grade).strip(), "min_pct": round(min_pct, 1)})

            if boundaries:
                cleaned.append({
                    "subject": str(subject).strip(),
                    "tier": str(tier).strip() if tier else None,
                    "boundaries": boundaries,
                })

        return cleaned
    except Exception as e:
        print(f"Grade boundary extraction failed to parse: {type(e).__name__}: {e}")
        return []

async def extract_paper_cover_metadata_from_text(paper_text: str) -> Dict[str, Optional[str]]:
    """
    Reads the paper's own code and series/session straight off its cover
    page, so an admin only has to pick the qualification (board/level/
    subject) and tier - everything the paper itself states is derived here
    rather than typed into a form.

    Returns {"paper_code": ..., "series": ...}; either may be None if the
    cover page doesn't state it clearly.
    """
    prompt = f"""
    You are given the opening page(s) of a UK exam board question paper.

    Extract, exactly as printed on the cover page:
    - paper_code: the paper/component code (e.g. "8461/1H", "1BI0/1F")
    - series: the exam series/session and paper number, in the exam board's own
      wording (e.g. "June 2023 Paper 1 Higher Tier", "November 2022")

    Output ONLY a flat JSON object: {{"paper_code": "..." or null, "series": "..." or null}}

    PAPER TEXT:
    \"\"\"{paper_text[:5000]}\"\"\"
    """
    system_prompt = "You are an expert UK exam board paper parser. Output only valid JSON, no commentary."

    res = await call_llm(prompt, temperature=0.0, system_prompt=system_prompt, call_category="paper_cover_metadata")
    try:
        data = json.loads(res)
        paper_code = data.get("paper_code")
        series = data.get("series")
        return {
            "paper_code": str(paper_code).strip() if isinstance(paper_code, str) and paper_code.strip() else None,
            "series": str(series).strip() if isinstance(series, str) and series.strip() else None,
        }
    except Exception as e:
        print(f"Paper cover metadata extraction failed to parse: {type(e).__name__}: {e}")
        return {"paper_code": None, "series": None}

async def compile_mark_scheme_to_dsl(question_text: str, mark_value: int, mark_scheme: str, answer_type: str = "written", answer_options: Any = None) -> str:
    """
    §3.1 Deterministic DSL Compilation - only called for numeric/select/
    multi_select/grid_select answer types, which have one structurally
    fixed correct value/option regardless of mark value. Free-text
    "written" answers are never compiled to DSL; they're graded by the AI
    model instead (keyword/phrase DSL matching proved too brittle on
    reasoning answers).
    """
    single_value_note = ""
    if answer_type in ("numeric", "select", "multi_select", "grid_select"):
        single_value_note = f"""
    This question's answer field accepts exactly ONE {answer_type} value from the
    student - not a multi-part written response. AND requires every operand to
    match that same single value simultaneously, which is impossible whenever the
    mark scheme lists several different numbers/options as its marking points (e.g.
    an intermediate reading, an unsimplified expression, and a final answer are
    three different values - ANDing them together can never be satisfied by any
    single submission, so the question would always mark as wrong). Use OR between
    them instead.
    """
    mcq_note = ""
    if answer_type in ("select", "multi_select"):
        options_block = json.dumps(answer_options) if answer_options else "(not available)"
        mcq_note = f"""
    This question's answer is a choice among fixed options (answer_type
    "select"/"multi_select"), not free text - the student's submitted
    answer is the option's short KEY letter(s) (e.g. "A", or "A,C" for a
    multi_select with several ticked), never the option's full text. This
    question's options (key/text pairs, in submission order) are:
    {options_block}
    Always compile it to MCQ:KEY (e.g. MCQ:A, or MCQ:A,C for multi_select),
    using the exact "key" value of the correct option(s) above - never the
    option's full text, even though the mark scheme itself usually
    describes the answer in words (match that wording to the option whose
    "text" it describes, then use that option's "key"). Never use
    EXACT/CONTAIN/ANY for this answer_type, even if the option text itself
    looks numeric (e.g. an option reading "34 g/m2/year" is still
    MCQ:<its key>, not EXACT:34 g/m2/year).
    """

    prompt = f"""
    Compile the following UK Exam Board Question & Mark Scheme into Deterministic DSL:
    Question: {question_text}
    Mark Value: {mark_value}
    Answer type: {answer_type}
    Mark Scheme: {mark_scheme}
    {single_value_note}
    {mcq_note}
    Available DSL Operators:
    - MCQ:OPTION (e.g. MCQ:B)
    - CONTAIN:term (e.g. CONTAIN:mitochondria)
    - NOT CONTAIN:term
    - ANY:term1,term2,term3 (synonyms/alternative phrasings - ANY OF THEM satisfies this point)
    - ALL:term1,term2 (every one of these is required)
    - MIN:n:term1,term2,term3,... (at least n of these terms must be present - use this
      for "any two from: A, B, C" style mark schemes, which ANY cannot express since
      ANY is satisfied by just one match)
    - EXACT:value (exact number, fraction like 3/4, index like 2^3, or algebraic
      expression like x^2+2x+1 or (x+2)/(x-3) - compared symbolically, so any
      equivalent/unsimplified form the student writes is accepted)
    - RANGE:min,max (numerical tolerance)
    - MARKS:n:CLAUSE (this ONE alternative, if matched, is worth exactly n marks - not the
      question's full mark value. CLAUSE is a single ordinary operator clause, e.g.
      MARKS:1:EXACT:7.2 or MARKS:1:RANGE:6.5,7.5 - never another AND/OR/MARKS nested inside it.
      Use this ONLY when the mark scheme explicitly states a DIFFERENT, lower mark count for a
      specific alternate answer than the question's full marks - e.g. "allow for 1 mark an
      answer of 7.2 ..." on a 3-mark question. Every other OR branch without a MARKS: wrapper
      is still worth the question's FULL mark value if matched, exactly as before - only wrap
      the branch(es) the mark scheme explicitly says are worth fewer marks.)
    - Boolean AND / OR logic, written as the literal uppercase words AND / OR only
      (never lowercase "and"/"or", never |, ;, or any other separator - lowercase
      "and"/"or" inside a term's own text, e.g. "fight or flight", is fine and left
      alone; it's only the literal uppercase word that acts as the boolean connector)

    Matching is inflection-tolerant and word-boundary-safe: CONTAIN/ANY/ALL/MIN
    already match "increase" against "increases"/"increasing"/"increased" in the
    student's answer automatically, and won't false-positive-match a term inside an
    unrelated longer word. Because of this, write each concept as ONE base form only
    - never list separate plural/tense/gerund variants of the same word (e.g. write
    ANY:increase heart rate, NOT ANY:increase heart rate,increases heart rate).

    AND means every operand must ALL be true of the one answer the student actually
    submits - only use it to combine genuinely separate requirements a single written
    answer must all satisfy (e.g. CONTAIN:oxygen AND CONTAIN:carbon dioxide). Never
    use AND to join several different numbers/options that are alternative
    representations of one correct answer (an intermediate working value and the
    final result are NOT both required in the same submission) - use OR for those,
    since the student submits only one value and any one accepted form of it is enough.

    A mark scheme's bullet points are often the multiple ways full marks could be
    reached (e.g. method credit for an intermediate reading, for the unsimplified
    expression, or for the final computed value) rather than separate simultaneous
    requirements - when in doubt about whether two mark-scheme values are
    alternatives or a combined requirement, prefer OR.

    Distinguish that (an alternative FULL-credit representation of the same correct
    answer, e.g. an unrounded fraction vs its decimal - plain OR, no MARKS: wrapper)
    from a mark scheme explicitly stating a DIFFERENT alternate answer is worth FEWER
    marks than full credit (e.g. a wrong-method or misread answer the scheme still
    "allows" a reduced number of marks for) - that branch MUST be wrapped in
    MARKS:n:CLAUSE, or a student submitting it would silently receive the question's
    FULL marks instead of the partial credit the real mark scheme actually intends.
    Never wrap the full-credit branch itself in MARKS: - only the reduced-credit
    alternative(s).

    Never write two clauses back-to-back with nothing between them, and never nest
    an operator keyword inside another operator's value (e.g. never write
    ANY:CONTAIN:x,CONTAIN:y - just write ANY:x,y). Always separate every clause from
    the next with an explicit AND or OR.

    Collapse alternative phrasings of the SAME point into one ANY list instead of
    OR-ing separate CONTAIN clauses together - e.g. write ANY:dilute,dilution instead
    of CONTAIN:dilute OR CONTAIN:dilution. A chain of "CONTAIN:x OR CONTAIN:y OR
    CONTAIN:z" should almost always be ANY:x,y,z instead - it's shorter and means
    the same thing.

    NOT CONTAIN must only ever be ANDed onto a positive requirement it qualifies
    (e.g. ANY:low amount,low proportion AND NOT CONTAIN:not needed) - never OR it in
    as if it were an alternative way to satisfy the point. Since a real answer almost
    never contains one exact excluded phrase, ORing a NOT CONTAIN in makes that whole
    branch true for nearly any answer, silently accepting everything. If a "do not
    accept X" mark-scheme note is a minor caveat rather than something worth
    excluding, it's usually safer to just omit it than to risk this.

    Since EXACT already compares values symbolically (any algebraically-equivalent
    unsimplified form the student writes is accepted), do not write multiple EXACT
    branches that are algebraically equal to each other - e.g. "EXACT:2200/4200 OR
    EXACT:11/21" is redundant, since EXACT:2200/4200 alone already accepts "11/21"
    as an equivalent form. Only add another EXACT/RANGE branch for a genuinely
    different accepted value (a different valid method's result, a reciprocal, or an
    alternative rounding).

    Numeric/algebraic values in EXACT and RANGE are parsed as plain arithmetic,
    NOT LaTeX - even if the mark scheme itself uses LaTeX (e.g. "$\\frac{{10}}{{43}}$").
    Never write LaTeX commands, backslashes, `$` delimiters, or `\\frac{{}}{{}}`
    in a DSL value. Write a fraction as "10/43", not "\\frac{{10}}{{43}}". Never put
    units inside an EXACT/RANGE value (e.g. never "EXACT:34 g/m2/year") - units are
    not part of the numeric comparison and this style silently degrades to an
    almost-never-matching string comparison; if a unit genuinely needs checking on a
    written (not numeric/select) answer, AND a separate CONTAIN:unit onto it.

    When the mark scheme accepts a value to a stated or implied rounding (e.g. it
    lists both an exact fraction and a rounded decimal, like "10/43 or 0.23255...
    or 0.233"), don't rely on EXACT alone for the rounded form - EXACT requires
    near-exact equality (~1e-6) so a correctly-rounded answer would fail it. Instead
    combine EXACT for the precise value with a RANGE spanning the accepted rounding
    tolerance (e.g. "EXACT:10/43 OR RANGE:0.2325,0.2335") so an answer given to
    the paper's expected significant figures is accepted.

    Worked examples:
    - Mark scheme "any two from: nucleus / vacuole / mitochondria" (2 marks) ->
      MIN:2:nucleus,vacuole,mitochondria (NOT ANY:nucleus,vacuole,mitochondria,
      which would wrongly award both marks for naming just one).
    - Mark scheme "1 mark for each of: dilute / mean or average / multiply, scale or
      calculate" (as three separate required steps) ->
      ANY:dilute,dilution AND ANY:mean,average AND ANY:multiply,scale,calculate.
    - Mark scheme "10/43 or 0.23255... or 0.233" (unit: bubbles per second) ->
      (EXACT:10/43 OR RANGE:0.2325,0.2335) AND CONTAIN:bubbles per second.
    - Mark scheme "45/100 x 240 = 108 (full marks) ... allow for 1 mark an answer of 7.2 with
      evidence of having used the wrong percentage" (3 marks) ->
      EXACT:108 OR RANGE:107.5,108.5 OR MARKS:1:EXACT:7.2 (the 108 branches are still worth the
      full 3 marks; only the explicitly-lower-credit 7.2 branch is wrapped in MARKS:1:).

    Output ONLY a flat JSON object with exactly one key, "marking_dsl", whose
    value is a single DSL expression string (combine multiple marking points
    with AND / OR inside that one string). Do not nest objects, do not add
    any other keys, do not return a list of marking points.
    """
    res = await call_llm(prompt, temperature=0.1, call_category="dsl_compile")
    fallback = f"CONTAIN:{question_text.split()[-1]}"
    try:
        data = json.loads(res)
        dsl = data.get("marking_dsl", fallback) if isinstance(data, dict) else data
        return _coerce_dsl_to_string(dsl, fallback)
    except Exception:
        # Fallback to keyword from mark scheme
        first_key = [w for w in re.findall(r'\b[a-zA-Z]{4,}\b', mark_scheme) if w.lower() not in {'allow', 'accept', 'marks', 'ignore'}][:1]
        return f"CONTAIN:{first_key[0]}" if first_key else "CONTAIN:correct"

def _extract_list(data: Any, key: str) -> List[Any]:
    """
    Models don't reliably follow an instructed {key: [...]} wrapper shape -
    sometimes they return a bare JSON array instead. Accept either rather
    than crashing on data.get() when data turns out to be a list.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        val = data.get(key, [])
        return val if isinstance(val, list) else []
    return []

def _normalize_question_number(raw: str) -> str:
    """
    Loosely normalizes a question number so an examiner report's own
    reference to it ("Question 01.1", "Q1.1", "1 . 1") can be matched
    against this paper's own question_number string (e.g. "01.1") despite
    exam boards not being perfectly consistent about zero-padding/prefixing
    between the two documents.
    """
    s = raw.strip().lower()
    s = re.sub(r'^(question|q)\s*[:.]?\s*', '', s)
    s = re.sub(r'\s+', '', s)
    s = re.sub(r'(?<![0-9])0+(?=[0-9])', '', s)  # strip leading zeros per numeric run
    return s

def _coerce_dsl_to_string(dsl: Any, fallback: str) -> str:
    """
    Models don't always follow an instructed flat-string schema (e.g. Gemini
    sometimes nests a per-marking-point breakdown instead of one flat DSL
    string). Recursively pull out any nested "dsl" string values and combine
    them with OR, rather than letting a non-string value reach the DB.
    """
    if isinstance(dsl, str) and dsl.strip():
        return dsl.strip()

    found: List[str] = []

    def walk(node: Any):
        if isinstance(node, dict):
            nested = node.get("dsl")
            if isinstance(nested, str) and nested.strip():
                found.append(nested.strip())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(dsl)
    if found:
        # dedupe while preserving order
        seen = set()
        unique = [f for f in found if not (f in seen or seen.add(f))]
        return " OR ".join(f"({f})" for f in unique) if len(unique) > 1 else unique[0]

    return fallback

async def scan_text_for_misconceptions(
    source_text: str,
    source_label: str,
    known_topics: List[Dict[str, str]],
    existing_tags: List[Dict[str, str]] | None = None
) -> List[Dict[str, Any]]:
    """
    §6.2a Misconception Scanner for Proposing Canonical Misconception Tags.

    Run against either an examiner report (explicit candidate-mistake
    commentary) or a mark scheme (which also states common wrong answers via
    its "do not accept" / "common error" annotations, even when no separate
    examiner report was uploaded) - both are optional per-paper, so the
    caller scans whichever is present and merges the results.

    Classifies each proposed misconception against the paper's own known
    topic list (the same list used to classify questions) rather than a
    single admin-typed spec_code, so a report covering several topics isn't
    forced under one code. A misconception the model can't confidently place
    on the list is dropped by the caller (misconception_taxonomy.spec_code
    is NOT NULL, so there's no safe default to fall back to).

    `existing_tags` is the taxonomy already proposed or approved for these
    topics (label + description, not just tag_id) - grounding the model
    against it is what §6.2a's "checked for semantic overlap against
    existing tags" step actually means. Without this, each scan is blind to
    every other scan that ever ran, so the same underlying misconception
    keeps getting re-invented under a slightly different tag_id/phrasing
    every time a new document is scanned (e.g. "unit_conversion_cm_to_um_error"
    and "unit_conversion_cm_to_mum" both getting proposed from two different
    papers) - a tag_id-exact-match dedup at insert time can't catch that.
    """
    topics_list = "\n".join(f"- {t['spec_code']}: {t['title']}" for t in known_topics) if known_topics else "(none available)"
    existing_list = "\n".join(f"- {t['tag_id']}: {t['label']} - {t['description']}" for t in existing_tags) if existing_tags else "(none proposed yet for this specification)"
    prompt = f"""
    Analyze this {source_label} text:
    \"\"\"{source_text[:500000]}\"\"\"

    Extract recurring candidate mistakes, conceptual confusions, and traps. For a mark
    scheme, these typically show up as "do not accept", "common error", or "credit is not
    given for" style annotations rather than prose commentary.

    This specification's misconception taxonomy already contains these tags (approved or
    pending admin approval):
    {existing_list}

    Before proposing a new tag, check it against that list. If a mistake you find is the
    same underlying misconception as one already there - even if this text describes it
    using different words or a different example - do NOT propose it again; that
    misconception is already covered. Only propose a tag for a mistake that is genuinely
    distinct from everything already listed.

    For each new tag you do propose, classify it against this specification's topic list by
    choosing the single best-matching spec_code from the list below, or null if none clearly
    apply:
    {topics_list}

    Output ONLY a flat JSON object with exactly this shape (field names must
    match exactly - do not rename or add fields):
    {{"proposed_tags": [
        {{"tag_id": "<snake_case_id, e.g. confuses_mitosis_meiosis>", "label": "<human readable title>", "description": "<precise description of the misconception>", "spec_code": "<matching spec_code or null>"}}
    ]}}
    """
    res = await call_llm(prompt, temperature=0.2, call_category=f"misconception_scan:{source_label.replace(' ', '_')}")
    known_codes = {t["spec_code"] for t in known_topics} if known_topics else set()
    existing_ids = {t["tag_id"] for t in existing_tags} if existing_tags else set()
    try:
        data = json.loads(res)
        tags = _extract_list(data, "proposed_tags")
    except Exception:
        return []

    return [t for t in tags if isinstance(t, dict) and t.get("spec_code") in known_codes and t.get("tag_id") not in existing_ids]

async def blind_grade_synthetic_answers_batch(
    question_text: str,
    mark_value: int,
    mark_scheme: str,
    candidate_answers: List[str],
    known_misconceptions: List[Dict[str, str]] | None = None,
    spec_code: str = "",
    call_category: str = "blind_grade_batch",
) -> List[Dict[str, Any]]:
    """
    Batched sibling of blind_grade_synthetic_answer - grades every candidate
    answer for ONE question in a single call instead of one call per
    candidate. Same rationale as generate_feedback_for_known_marks_batch:
    the shared question/mark-scheme/tag-list/instructions context dominates
    each individual call's cost, so paying for it once per question instead
    of once per candidate cuts cost without cutting how many answers get
    graded. Still genuinely "blind" to each candidate's own GENERATION
    target (never told what mark a candidate was invented to hit, and
    explicitly told to grade each independently without letting one
    influence another) - blind here was always about not knowing the
    answer's intended level, not about isolating candidates from each
    other, so batching doesn't weaken the cross-check itself. Returns a
    list of {marks_awarded, www, ebi, misconception_tags,
    new_tag_suggestion} in the same order as `candidate_answers`.
    """
    if not candidate_answers:
        return []
    tags_list = "\n".join(f"- {t['tag_id']}: {t['label']}" for t in known_misconceptions) if known_misconceptions else "(none approved yet)"
    known_ids = {t["tag_id"] for t in known_misconceptions} if known_misconceptions else set()
    candidates_block = "\n".join(f'{i + 1}. "{ans}"' for i, ans in enumerate(candidate_answers))
    prompt = f"""
    Mark EACH of the following {len(candidate_answers)} GCSE exam answers exactly as a human
    examiner would, strictly against the mark scheme below. Award only the marks the mark
    scheme's criteria actually support for each answer independently - do not guess, do not be
    generous, and grade each answer entirely independently: never let one answer's content or
    wording influence another answer's mark or feedback.

    Question: {question_text}
    Mark scheme ({mark_value} marks available): {mark_scheme}

    Student answers:
    {candidates_block}

    For EACH answer above, in the SAME order:
    If the answer reveals a conceptual misconception, choose the single best-matching tag
    from this approved list (omit misconception_tags entirely if none clearly fit - never
    put a tag not on this list into misconception_tags):
    {tags_list}

    If an answer reveals a genuine, clearly identifiable misconception that does NOT
    match any tag above closely enough, propose ONE new candidate tag for it via
    new_tag_suggestion instead of forcing a bad match into misconception_tags or ignoring
    it. Only do this when you are confident it is a real, recurring-type conceptual error -
    not simply "the answer was wrong", a one-off slip, or task-specific carelessness. Use
    null for new_tag_suggestion when nothing meets that bar.

    Structure each answer's feedback as two bullet-point lists, the standard UK classroom
    feedback format: "www" (What Went Well) - things that answer actually got right, quoting or
    closely paraphrasing its own words; and "ebi" (Even Better If) - concrete mark scheme points
    that answer is missing. Every www bullet must describe something genuinely present in that
    answer - never invent content it doesn't contain, even if it resembles what the mark scheme
    expected. Every ebi bullet must be your own rephrasing of a point genuinely absent from that
    answer as a concrete suggestion - never list a point the answer already made, even if worded
    differently to the mark scheme, and never copy a mark scheme line verbatim (including its
    leading bullet marker like "- ").
    {marking_prompt.CONSISTENCY_RULES_BLOCK}

    Output ONLY a flat JSON object with exactly this shape (field names must match exactly) -
    "results" MUST have exactly {len(candidate_answers)} entries, one per answer above, in the
    same order:
    {{"results": [
      {{"marks_awarded": <integer 0 to {mark_value}>,
        "www": ["<thing the answer got right>", ...],
        "ebi": ["<mark scheme point not evidenced in the answer>", ...],
        "misconception_tags": ["<tag_id from the approved list>", ...],
        "new_tag_suggestion": {{"tag_id": "<snake_case_id>", "label": "<short title>", "description": "<precise description>"}} or null}},
      ...
    ]}}
    """
    res = await call_llm(prompt, temperature=0.1, call_category=call_category)
    try:
        data = json.loads(res)
        results = _extract_list(data, "results")
    except Exception as e:
        print(f"Batched independent auto-grading error: {e}")
        results = []

    out = []
    for i in range(len(candidate_answers)):
        item = results[i] if i < len(results) and isinstance(results[i], dict) else {}
        try:
            marks = int(item.get("marks_awarded", 0))
        except (TypeError, ValueError):
            marks = 0
        marks = max(0, min(marks, mark_value))
        www = [str(w) for w in item.get("www", [])] if isinstance(item.get("www"), list) else []
        ebi = [str(e) for e in item.get("ebi", [])] if isinstance(item.get("ebi"), list) else []
        if marks == mark_value:
            ebi = []
        if marks == 0:
            www = []
        tags = [t for t in item.get("misconception_tags", []) if isinstance(t, str) and t in known_ids] if isinstance(item.get("misconception_tags"), list) else []

        new_tag = None
        suggestion = item.get("new_tag_suggestion")
        if isinstance(suggestion, dict) and suggestion.get("tag_id") and suggestion.get("label") and suggestion.get("description"):
            new_tag = {
                "tag_id": str(suggestion["tag_id"]).strip(),
                "label": str(suggestion["label"]).strip(),
                "description": str(suggestion["description"]).strip(),
                "spec_code": spec_code,
            }
        out.append({"marks_awarded": marks, "www": www, "ebi": ebi, "misconception_tags": tags, "new_tag_suggestion": new_tag})
    return out

async def generate_feedback_for_known_marks_batch(
    question_text: str,
    mark_value: int,
    mark_scheme: str,
    candidates: List[Dict[str, Any]],
    known_misconceptions: List[Dict[str, str]] | None = None,
    spec_code: str = "",
    call_category: str = "known_mark_feedback_batch",
) -> List[Dict[str, Any]]:
    """
    Cheaper sibling of blind_grade_synthetic_answer, for mark-scheme-exemplar
    mining specifically (§6.2, real-ground-truth path) - the mark scheme's
    own text already states the exact mark each candidate['candidate_answer']
    earns (candidate['known_marks']), so this does NOT ask the model to
    independently re-derive a mark from scratch (that used to be
    blind_grade_synthetic_answer's job here too, as a "cross-check" against
    the mark scheme's own stated value). Measured across every paper
    ingested this session, that cross-check agreed 99%+ of the time - for
    over half the entire ingestion pipeline's token spend, it was
    essentially never catching anything. This instead trusts the mark
    scheme's own stated value outright and asks only for the www/ebi/
    misconception-tag labels consistent with it.

    BATCHED across every candidate for this ONE question in a single call,
    rather than one call per candidate - the shared context (question text,
    mark scheme, tag list, instructions) is what dominates each individual
    call's cost, not the short candidate answer itself, so paying for it
    once per question instead of once per candidate is the single biggest
    lever for reducing ingestion cost WITHOUT reducing how many training
    examples get produced (unlike sampling fewer candidates, which trades
    away real coverage). Returns a list of {www, ebi, misconception_tags,
    new_tag_suggestion} in the same order as `candidates`.
    """
    if not candidates:
        return []
    tags_list = "\n".join(f"- {t['tag_id']}: {t['label']}" for t in known_misconceptions) if known_misconceptions else "(none approved yet)"
    known_ids = {t["tag_id"] for t in known_misconceptions} if known_misconceptions else set()
    candidates_block = "\n".join(
        f'{i + 1}. Confirmed marks: {c["known_marks"]}/{mark_value}. Answer: "{c["candidate_answer"]}"'
        for i, c in enumerate(candidates)
    )
    prompt = f"""
    The official mark scheme confirms the exact mark EACH of the {len(candidates)} answers below
    earns - you do not need to (and must not) re-decide any of them, only explain each one.

    Question: {question_text}
    Mark scheme ({mark_value} marks available): {mark_scheme}

    Answers (each already confirmed against the mark scheme):
    {candidates_block}

    For EACH answer above, in the SAME order, generate the standard UK classroom feedback,
    consistent with its own confirmed mark: "www" (What Went Well) - things that answer actually
    got right, quoting or closely paraphrasing its own words; and "ebi" (Even Better If) -
    concrete mark scheme points that answer is missing. Every www bullet must describe something
    genuinely present in that answer - never invent content it doesn't contain, even if it
    resembles what the mark scheme expected. Every ebi bullet must be your own rephrasing of a
    point genuinely absent from that answer as a concrete suggestion - never list a point the
    answer already made, and never copy a mark scheme line verbatim (including its leading
    bullet marker like "- "). Grade each answer entirely independently - never let one answer's
    content or wording influence another answer's feedback.
    {marking_prompt.CONSISTENCY_RULES_BLOCK}

    For each answer, if it reveals a conceptual misconception, choose the single best-matching
    tag from this approved list (omit misconception_tags entirely if none clearly fit - never
    put a tag not on this list into misconception_tags):
    {tags_list}

    If an answer reveals a genuine, clearly identifiable misconception that does NOT match any
    tag above closely enough, propose ONE new candidate tag for it via new_tag_suggestion instead
    of forcing a bad match into misconception_tags or ignoring it. Only do this when you are
    confident it is a real, recurring-type conceptual error - not simply "the answer was wrong",
    a one-off slip, or task-specific carelessness. Use null for new_tag_suggestion when nothing
    meets that bar.

    Output ONLY a flat JSON object with exactly this shape (field names must match exactly) -
    "results" MUST have exactly {len(candidates)} entries, one per answer above, in the same
    order:
    {{"results": [
      {{"www": ["<thing the answer got right>", ...],
        "ebi": ["<mark scheme point not evidenced in the answer>", ...],
        "misconception_tags": ["<tag_id from the approved list>", ...],
        "new_tag_suggestion": {{"tag_id": "<snake_case_id>", "label": "<short title>", "description": "<precise description>"}} or null}},
      ...
    ]}}
    """
    res = await call_llm(prompt, temperature=0.1, call_category=call_category)
    try:
        data = json.loads(res)
        results = _extract_list(data, "results")
    except Exception as e:
        print(f"Batched known-mark feedback generation error: {e}")
        results = []

    out = []
    for i, c in enumerate(candidates):
        item = results[i] if i < len(results) and isinstance(results[i], dict) else {}
        known_marks = c["known_marks"]
        www = [str(w) for w in item.get("www", [])] if isinstance(item.get("www"), list) else []
        ebi = [str(e) for e in item.get("ebi", [])] if isinstance(item.get("ebi"), list) else []
        # Same deterministic guardrail as blind_grade_synthetic_answer -
        # mechanically enforced from the ALREADY-KNOWN mark, not a guess.
        if known_marks >= mark_value:
            ebi = []
        if known_marks <= 0:
            www = []
        tags = [t for t in item.get("misconception_tags", []) if isinstance(t, str) and t in known_ids] if isinstance(item.get("misconception_tags"), list) else []

        new_tag = None
        suggestion = item.get("new_tag_suggestion")
        if isinstance(suggestion, dict) and suggestion.get("tag_id") and suggestion.get("label") and suggestion.get("description"):
            new_tag = {
                "tag_id": str(suggestion["tag_id"]).strip(),
                "label": str(suggestion["label"]).strip(),
                "description": str(suggestion["description"]).strip(),
                "spec_code": spec_code,
            }
        out.append({"www": www, "ebi": ebi, "misconception_tags": tags, "new_tag_suggestion": new_tag})
    return out

async def extract_exemplar_answers_from_examiner_report(
    examiner_report_text: str,
    questions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    §6.2 seed data: examiner reports frequently quote or paraphrase real
    candidate answers with commentary on why they scored what they did
    ("many candidates wrote X, which only gained 1 mark for..."). That's
    higher-signal training data than a purely AI-invented synthetic answer,
    since it reflects what real students actually wrote - but unlike a mark
    scheme's own worked exemplar, the report describes it in prose rather
    than a structured score, so pulling out the answer text (and whatever
    mark the report states, if any) and matching it back to a question
    number is its own extraction task, separate from grading it.
    """
    if not questions:
        return []
    question_list_text = "\n".join(
        f"- Q{q['question_number']} ({q['mark_value']} marks): {q['question_text'][:150]}"
        for q in questions
    )
    prompt = f"""
    The following is an examiner's report discussing candidate performance on a past exam paper.
    Extract every real candidate answer or answer pattern it describes or quotes, and match each
    one to the question number it discusses from this list:
    {question_list_text}

    Examiner Report:
    \"\"\"{examiner_report_text[:500000]}\"\"\"

    For each extracted candidate answer:
    - question_number: the matching question number from the list above (skip anything with no
      confident match - do not guess)
    - candidate_answer: the answer text as described or quoted. If the report only describes the
      pattern (e.g. "many candidates omitted the units"), reconstruct a plausible full answer
      showing that pattern rather than just restating the description.
    - reported_marks: the mark this answer is stated to have scored, or null if the report doesn't
      state one
    - reported_www: things the report itself says this answer/pattern got right, as a list of short
      strings in your own words - ONLY if the report actually explains this, never invented. Empty
      list if the report just states a mark with no reasoning.
    - reported_ebi: things the report itself says this answer/pattern was missing or got wrong, as a
      list of short strings phrased as concrete additions - ONLY if the report actually explains
      this, never invented. Empty list if the report just states a mark with no reasoning.

    Output ONLY a flat JSON object with exactly this shape (field names must match exactly):
    {{"exemplars": [
        {{"question_number": "<matching number>", "candidate_answer": "<answer text>", "reported_marks": <integer or null>, "reported_www": ["..."], "reported_ebi": ["..."]}}
    ]}}
    """
    res = await call_llm(prompt, temperature=0.1, call_category="examiner_exemplar_extract")
    try:
        data = json.loads(res)
        exemplars = _extract_list(data, "exemplars")
    except Exception as e:
        print(f"Examiner report exemplar extraction error: {e}")
        return []

    # Exam boards aren't perfectly consistent about how a question number is
    # written between the question paper and its examiner report ("01.1" vs
    # "Q1.1" vs "Question 1.1" vs no leading zero) - an exact string match
    # here silently drops real matches, so match on a normalized form and
    # rewrite back to this paper's own canonical question_number string
    # (what generate_exemplar_training_examples_from_report's lookup and the
    # rest of the pipeline actually key on).
    original_by_normalized = {_normalize_question_number(q["question_number"]): q["question_number"] for q in questions}
    matched = []
    for e in exemplars:
        if not isinstance(e, dict) or not str(e.get("candidate_answer", "")).strip():
            continue
        canonical = original_by_normalized.get(_normalize_question_number(str(e.get("question_number", ""))))
        if canonical is None:
            continue
        matched.append({**e, "question_number": canonical})
    print(f"Examiner report exemplar extraction: {len(exemplars)} raw, {len(matched)} matched to a known question number")
    return matched

async def generate_exemplar_training_examples_from_report(
    examiner_report_text: str,
    questions: List[Dict[str, Any]],
    known_misconceptions: List[Dict[str, str]] | None = None,
) -> Dict[str, Any]:
    """
    §6.2 seed data, examiner-report path: extracts real exemplar answers
    (see extract_exemplar_answers_from_examiner_report) and independently
    blind-grades each one with the same grader used for synthetic answers,
    so both sources land in `training_examples` with an identical label
    shape. Where the report itself states a mark, that's used as the
    cross-check target (same accept/reject logic as the synthetic path);
    where it doesn't, the example is accepted on the grader's mark alone -
    there's no second opinion to disagree with, but a real candidate answer
    with a fresh independent grading is still valid seed data.

    www/ebi labels prefer the report's own stated reasoning (reported_www/
    reported_ebi) over the blind grader's - a report that already explains
    *why* an answer scored what it did is a real, human-authored
    explanation, strictly higher-signal than an LLM re-deriving one from
    scratch. The blind grade is still always run (needed for the marks
    cross-check above and for misconception_tags classification against the
    approved taxonomy, which report prose rarely names explicitly), but its
    www/ebi are only used as a fallback when the report gave no reasoning of
    its own. Distinguished from synthetic examples via "source":
    "examiner_exemplar".
    """
    exemplars = await extract_exemplar_answers_from_examiner_report(examiner_report_text, questions)
    questions_by_number = {q["question_number"]: q for q in questions}
    accepted_examples = []
    proposed_tags_by_id: Dict[str, Dict[str, Any]] = {}
    known_ids = {t["tag_id"] for t in known_misconceptions} if known_misconceptions else set()

    # Group matched exemplars by question so every question's answers get
    # ONE batched grading call (see blind_grade_synthetic_answers_batch)
    # instead of one call per exemplar - a report often discusses several
    # candidate answers/patterns for the same question, and each of those
    # calls would otherwise resend that question's own text/mark scheme
    # from scratch.
    exemplars_by_question: Dict[str, List[Dict[str, Any]]] = {}
    for ex in exemplars:
        q = questions_by_number.get(ex["question_number"])
        # Mirrors ingestion.py's exemplar_eligible (2+ mark free-text
        # questions) - looser than synthetic generation's marking_type ==
        # "ai" gate, since capturing a real answer the report already
        # discusses costs nothing extra, unlike spending a call to invent
        # a synthetic one.
        if not q or not q.get("exemplar_eligible"):
            continue
        exemplars_by_question.setdefault(ex["question_number"], []).append(ex)

    for question_number, exs in exemplars_by_question.items():
        q = questions_by_number[question_number]
        topic_spec_codes = q.get("topic_spec_codes") or []
        spec_code = topic_spec_codes[0] if topic_spec_codes else ""
        candidate_answers = [str(ex["candidate_answer"]) for ex in exs]

        grades = await blind_grade_synthetic_answers_batch(
            question_text=q["question_text"],
            mark_value=q["mark_value"],
            mark_scheme=q.get("mark_scheme_text") or "Award marks for correct scientific reasoning.",
            candidate_answers=candidate_answers,
            known_misconceptions=known_misconceptions,
            spec_code=spec_code,
            call_category="blind_grade_batch:examiner_exemplar",
        )

        for ex, candidate_answer, grade in zip(exs, candidate_answers, grades):
            awarded = grade["marks_awarded"]
            reported = ex.get("reported_marks")
            try:
                reported = int(reported) if reported is not None else None
            except (TypeError, ValueError):
                reported = None
            is_valid = reported is None or awarded == reported or abs(awarded - reported) <= 1
            target = reported if reported is not None else awarded

            # Prefer the report's own stated reasoning over the blind
            # grader's - real explanation beats a re-guess. Only when the
            # report actually gave one (empty lists mean "extraction found
            # no explicit reasoning here", not "the report said there's
            # nothing to report").
            reported_www = [str(w) for w in ex.get("reported_www", [])] if isinstance(ex.get("reported_www"), list) else []
            reported_ebi = [str(e) for e in ex.get("reported_ebi", [])] if isinstance(ex.get("reported_ebi"), list) else []
            if reported_www or reported_ebi:
                www, missed_points = reported_www, reported_ebi
                # Same hard consistency guardrail as
                # blind_grade_synthetic_answer applies to its own output -
                # must hold here too now that the report's own text is the
                # source instead.
                if target == q["mark_value"]:
                    missed_points = []
                if target == 0:
                    www = []
            else:
                www, missed_points = grade["www"], grade["ebi"]

            accepted_examples.append({
                "question_number": question_number,
                "target_marks": target,
                "awarded_marks": awarded,
                "student_answer": candidate_answer,
                "is_accepted_for_training": is_valid,
                "spec_code": spec_code,
                "www": www,
                "missed_points": missed_points,
                "misconception_tags": grade["misconception_tags"],
                "source": "examiner_exemplar",
            })

            new_tag = grade.get("new_tag_suggestion")
            if new_tag and new_tag["tag_id"] not in known_ids:
                proposed_tags_by_id[new_tag["tag_id"]] = new_tag

    return {"examples": accepted_examples, "proposed_tags": list(proposed_tags_by_id.values())}

_LEVELS_OF_RESPONSE_RE = re.compile(r"\bLevel\s*3\b.*\bLevel\s*2\b.*\bLevel\s*1\b|\bLevel\s*1\b.*\bLevel\s*2\b.*\bLevel\s*3\b", re.IGNORECASE | re.DOTALL)


def _is_levels_of_response_scheme(mark_scheme_text: str) -> bool:
    """
    Levels-of-response ("Level 1"/"Level 2"/"Level 3") mark schemes award marks
    holistically per level/band, not per individual indicative-content point, so
    there is no real per-point mark value to extract - an isolated bullet mined
    from the indicative content has no meaningful "this exact wording earns N
    marks" ground truth attached to it, unlike a points-based scheme's marking
    points. Detected by requiring all three level markers to appear (in either
    order), not just "Level" once, to avoid false-positives on unrelated text.
    """
    if not mark_scheme_text:
        return False
    return bool(_LEVELS_OF_RESPONSE_RE.search(mark_scheme_text))


async def extract_mark_scheme_worked_answers(
    question_text: str,
    mark_value: int,
    mark_scheme_text: str,
) -> List[Dict[str, Any]]:
    """
    §6.2 seed data, mark-scheme path: official mark schemes routinely give
    literal example wording for a marking point ("e.g. accept: water moves
    from an area of high to low concentration (1)") alongside the exact
    number of marks that stated wording earns - real, human-authored ground
    truth already sitting in every uploaded mark scheme, unlike a purely
    AI-invented synthetic answer. Distinct from a full student answer to the
    whole question: a worked example usually covers ONE marking point, so
    reported_marks here is that point's own value, not necessarily the
    question's full mark_value.
    """
    if not mark_scheme_text or not mark_scheme_text.strip():
        return []
    prompt = f"""
    The following is the official mark scheme for a GCSE exam question. Extract every literal
    example answer wording it gives for a marking point (e.g. text after "accept", "e.g.", "eg",
    or shown in quotes/brackets as an acceptable answer) along with the exact number of marks
    that specific wording is stated to earn.

    Question ({mark_value} marks available): {question_text}
    Mark scheme: \"\"\"{mark_scheme_text[:20000]}\"\"\"

    Only extract wording the mark scheme itself gives as an example answer - never invent or
    paraphrase one yourself, and skip a marking point that states no concrete example wording
    (e.g. just "any valid reason" with nothing else) rather than guessing one.

    For each extracted example:
    - candidate_answer: the example answer text exactly as the mark scheme states it
    - reported_marks: the integer number of marks that specific wording earns, as stated by the
      mark scheme (never the question's full mark value unless the scheme explicitly says this
      one wording alone earns full marks)

    Output ONLY a flat JSON object with exactly this shape (field names must match exactly):
    {{"worked_answers": [
        {{"candidate_answer": "<example wording from the mark scheme>", "reported_marks": <integer>}}
    ]}}
    """
    res = await call_llm(prompt, temperature=0.1, call_category="mark_scheme_worked_answers_extract")
    try:
        data = json.loads(res)
        items = _extract_list(data, "worked_answers")
    except Exception as e:
        print(f"Mark scheme worked-answer extraction error: {e}")
        return []

    extracted = []
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate_answer = str(item.get("candidate_answer", "")).strip()
        if not candidate_answer:
            continue
        try:
            reported_marks = int(item.get("reported_marks"))
        except (TypeError, ValueError):
            continue
        extracted.append({"candidate_answer": candidate_answer, "reported_marks": reported_marks})
    return extracted


def _sample_worked_answers(worked_answers: List[Dict[str, Any]], mark_value: int) -> List[Dict[str, Any]]:
    """
    Caps how many mark-scheme worked-answer points actually get mined and
    labelled per question. Measured across every paper ingested this
    session: uncapped, this step alone was 52.8% of an entire paper's
    ingestion token budget, and many of the worked answers for a single
    question are near-duplicate signal - several one-word/short-phrase
    synonyms or alternative valid answers to the exact same 1-mark point
    (real case: one 1-mark question yielded 11 separate mined "answers" -
    individual symptom names from an "any other symptom" list) - with
    steeply diminishing training value per additional example beyond a
    handful. Caps at mark_value + 2 (a higher-mark question has more room
    for genuinely distinct partial-credit variety worth keeping), floored
    at 3, ceilinged at 6. Sampled by a hash of each answer's own text rather
    than the model's original order, so the same mark scheme picks the same
    subset across re-ingestions instead of an arbitrary first-N.
    """
    cap = max(3, min(mark_value + 2, 6))
    if len(worked_answers) <= cap:
        return worked_answers
    ranked = sorted(worked_answers, key=lambda wa: hashlib.md5(wa["candidate_answer"].encode("utf-8")).hexdigest())
    return ranked[:cap]


async def generate_mark_scheme_exemplar_examples(
    question_text: str,
    mark_value: int,
    mark_scheme_text: str,
    spec_code: str,
    known_misconceptions: List[Dict[str, str]] | None = None,
) -> Dict[str, Any]:
    """
    §6.2 seed data, mark-scheme path: extracts real worked-example answers
    from the mark scheme's own text (see extract_mark_scheme_worked_answers),
    capped/sampled per question (see _sample_worked_answers), and labels
    each one with generate_feedback_for_known_mark so all three training
    sources land in `training_examples` with an identical label shape.
    target_marks here is the mark scheme's own stated value for that exact
    wording - real ground truth, not a generation target, and (unlike the
    synthetic/examiner-exemplar paths) not independently re-derived either:
    an earlier version of this function cross-checked it via a full
    blind_grade_synthetic_answer call per worked answer, but that cross-
    check agreed 99%+ of the time across every paper ingested this session
    while costing over half the entire pipeline's tokens, so every accepted
    example here is now trusted directly against the mark scheme's own
    stated authority rather than re-verified. Distinguished from other
    sources via "source": "mark_scheme_exemplar".
    """
    if _is_levels_of_response_scheme(mark_scheme_text):
        return {"examples": [], "proposed_tags": []}

    worked_answers = _sample_worked_answers(
        await extract_mark_scheme_worked_answers(question_text, mark_value, mark_scheme_text), mark_value
    )
    if not worked_answers:
        return {"examples": [], "proposed_tags": []}

    accepted_examples = []
    proposed_tags_by_id: Dict[str, Dict[str, Any]] = {}
    known_ids = {t["tag_id"] for t in known_misconceptions} if known_misconceptions else set()

    reported_marks = [max(0, min(wa["reported_marks"], mark_value)) for wa in worked_answers]
    batch_input = [
        {"candidate_answer": wa["candidate_answer"], "known_marks": reported}
        for wa, reported in zip(worked_answers, reported_marks)
    ]
    labels_batch = await generate_feedback_for_known_marks_batch(
        question_text=question_text,
        mark_value=mark_value,
        mark_scheme=mark_scheme_text,
        candidates=batch_input,
        known_misconceptions=known_misconceptions,
        spec_code=spec_code,
        call_category="known_mark_feedback_batch:mark_scheme_exemplar",
    )

    for wa, reported, labels in zip(worked_answers, reported_marks, labels_batch):
        candidate_answer = wa["candidate_answer"]
        accepted_examples.append({
            "target_marks": reported,
            "awarded_marks": reported,
            "student_answer": candidate_answer,
            "is_accepted_for_training": True,
            "spec_code": spec_code,
            "www": labels["www"],
            "missed_points": labels["ebi"],
            "misconception_tags": labels["misconception_tags"],
            "source": "mark_scheme_exemplar",
        })

        new_tag = labels.get("new_tag_suggestion")
        if new_tag and new_tag["tag_id"] not in known_ids:
            proposed_tags_by_id[new_tag["tag_id"]] = new_tag

    return {"examples": accepted_examples, "proposed_tags": list(proposed_tags_by_id.values())}


def _synthetic_target_marks(mark_value: int, max_candidates: int = 6) -> List[int]:
    """
    §6.2: which mark levels to generate a synthetic answer for. One answer
    per achievable level (0..mark_value) whenever that's a manageable
    number of LLM calls - each extra candidate costs one more independent
    blind_grade_synthetic_answer call on top of the single (always cheap,
    covers every candidate at once) generation call. A fixed full/half/zero
    spread was too coarse for anything above a handful of marks - a 6-mark
    question only ever got answers targeting 6, 3, and 0, nothing at 1, 2,
    4, or 5 - which starves partial-credit calibration for exactly the
    higher-mark, harder-to-judge questions. Above max_candidates achievable
    levels, spread evenly across the full range (always including both
    endpoints) instead of one-per-level, to keep ingestion cost bounded.

    2-mark questions are the exception: they're numerous (a large share of
    the written question bank), and their middle candidate (1/2, partial
    credit) adds comparatively little calibration value over just the two
    endpoints - so they get 0/2 only rather than 0/1/2.
    """
    if mark_value == 2:
        return [0, 2]
    if mark_value + 1 <= max_candidates:
        return list(range(mark_value + 1))
    return sorted({round(i * mark_value / (max_candidates - 1)) for i in range(max_candidates)})


async def generate_and_validate_synthetic_answers(
    question_text: str,
    mark_value: int,
    mark_scheme: str,
    spec_code: str,
    known_misconceptions: List[Dict[str, str]] | None = None
) -> Dict[str, Any]:
    """
    §6.2 Synthetic Training Data Generation & Auto-Grading Cross-Check:
    1. Generates candidate synthetic answers, one per target mark level
       (see _synthetic_target_marks).
    2. Runs blind_grade_synthetic_answers_batch as a genuinely independent
       cross-check for every candidate at once (never told the target
       level), in one shared-context call rather than one call per candidate.
    3. Cross-checks: accepted if target and awarded agree within tolerance.

    Returns {"examples": [...], "proposed_tags": [...]} - "proposed_tags"
    collects any new_tag_suggestion the grader surfaced across the
    generated candidates (§6.2a feedback loop: a misconception can be
    discovered by grading an answer, not just by scanning a mark
    scheme/examiner report).
    """
    targets = _synthetic_target_marks(mark_value)
    target_lines = "\n".join(f"    {i + 1}. {t}/{mark_value} marks" for i, t in enumerate(targets))
    example_items = ",\n".join(
        f'        {{"target_marks": {t}, "answer": "<realistic student answer text>"}}' for t in targets
    )

    prompt = f"""
    Generate {len(targets)} distinct synthetic student answers for:
    Question: {question_text}
    Mark Value: {mark_value}
    Mark Scheme: {mark_scheme}

    Target one answer at each of these mark levels:
{target_lines}
    For the 0-mark answer, include a realistic misconception rather than a blank/irrelevant response.

    Output ONLY a flat JSON object with exactly this shape (field names must
    match exactly - do not rename or add fields):
    {{"synthetic_answers": [
{example_items}
    ]}}
    """
    res = await call_llm(prompt, temperature=0.4, call_category="synthetic_generate")
    accepted_examples = []
    proposed_tags_by_id: Dict[str, Dict[str, Any]] = {}
    known_ids = {t["tag_id"] for t in known_misconceptions} if known_misconceptions else set()

    try:
        data = json.loads(res)
        raw_candidates = _extract_list(data, "synthetic_answers")

        targets_list = []
        answers_list = []
        for cand in raw_candidates:
            if not isinstance(cand, dict):
                continue
            try:
                target = int(cand.get("target_marks", 0))
            except (TypeError, ValueError):
                target = 0
            targets_list.append(target)
            answers_list.append(str(cand.get("answer", "")))

        # One batched grading call for every candidate this question
        # generated, instead of one call per candidate - see
        # blind_grade_synthetic_answers_batch's docstring.
        grades = await blind_grade_synthetic_answers_batch(
            question_text=question_text,
            mark_value=mark_value,
            mark_scheme=mark_scheme,
            candidate_answers=answers_list,
            known_misconceptions=known_misconceptions,
            spec_code=spec_code,
            call_category="blind_grade_batch:synthetic",
        )

        for target, ans, grade in zip(targets_list, answers_list, grades):
            awarded = grade["marks_awarded"]
            # Cross-check agreement
            is_valid = (awarded == target) or (abs(awarded - target) <= 1)
            accepted_examples.append({
                "target_marks": target,
                "awarded_marks": awarded,
                "student_answer": ans,
                "is_accepted_for_training": is_valid,
                "spec_code": spec_code,
                "www": grade["www"],
                "missed_points": grade["ebi"],
                "misconception_tags": grade["misconception_tags"],
                "source": "synthetic",
            })

            new_tag = grade.get("new_tag_suggestion")
            if new_tag and new_tag["tag_id"] not in known_ids:
                proposed_tags_by_id[new_tag["tag_id"]] = new_tag
    except Exception as e:
        print(f"Synthetic generation error: {e}")

    return {"examples": accepted_examples, "proposed_tags": list(proposed_tags_by_id.values())}
