# Acexam — Application Build Plan

AI-powered exam revision platform for UK students, built as a genuine alternative to tools like Revise2 and MedlyAI. This document covers product scope, example database schemas, and a phased build roadmap. Deployment/infrastructure topology (cluster layout, DNS, autoscaling) is intentionally out of scope — this is about the application itself.

---

## 1. Vision & Competitive Positioning

Acexam's differentiation rests on three pillars:

- **100% Free for Everyone** — no paid tier, no subscription fees, no paywalls, and no energy quotas. Powered by an ingestion-first architecture and ultra-lightweight self-hosted models.
- **Mark-scheme-accurate marking** — questions are sourced from real past papers with real mark schemes, so feedback reflects how exam boards actually award marks.
- **Near-instant AI feedback** — fine-tuned ultra-lightweight models (1.5B–3B parameter class) return structured marking feedback in under 300ms, making feedback feel instant.

### 1.1 Differentiation vs Competitors (Revise2 & Medly.ai)

Acexam combines the best elements of top competitors — matching Revise2 on adaptive weakness tracking and Medly.ai on an authentic exam-paper canvas interface — while remaining 100% free with zero paywalls.

| Dimension | Revise2 | Medly.ai | Acexam |
|---|---|---|---|
| Cost | Entirely free | Subscription paywall | **100% Free for everyone**. Self-hosted 1.5B–3B models eliminate token billing. Zero paywalls, zero energy meters. |
| Response Speed | Standard API (~2–5s) | Standard API (~2–5s) | **Near-instant (<300ms)**: Lightweight fine-tuned models + vLLM prefix caching stream feedback almost instantly. |
| Exam Canvas UI | Standard Web Form | Paper-like Canvas workspace | **Authentic Exam Paper Canvas (§3.8)**: Renders questions in true board layout with lined text zones, HTML5 pen/stylus drawing canvas, instant OCR, and inline tick/cross visual annotations. |
| Personalization | Topic weakness scores | Practice queues | **Misconception Memory (§3.7)**: Tracks specific recurring mistakes per student per spec over time, automatically targeting practice to fixed misconceptions. |
| Question Freshness | Generated on demand | Static past papers | **Verified Variants (§3.6)**: Generates fresh question variants derived from verified bank mark schemes without sacrificing accuracy. |
| Photo / Image Marking | Handwritten OCR | Built-in canvas inking | **Dual Path (§3.4 & §3.8)**: In-app digital pen canvas OR uploaded photo OCR, routed through the same ultra-fast marking engine. |

The core build pillars are **authentic exam-paper canvas UI**, **image/ink marking**, **ultra-fast lightweight spec-code models**, and **persistent misconception memory**.

---

## 2. Product & Service Model

### 2.1 100% Free Access & Rate Protection

Because live marking runs exclusively on ultra-lightweight self-hosted models (§6), the per-inference cost is near-zero. All features are **100% free for all students**, with no subscriptions, energy limits, or paywalls. Sliding-window rate limiting in Redis prevents automated scraping or bot abuse.

| Access Tier | Cost | Marking Quota | Response Latency | Rate Limit |
|---|---|---|---|---|
| **All Students** | **£0 (Free)** | **Unlimited** | **Near-Instant (<300ms)** | 60 requests / minute |

| Action | Cost to Student | AI Involved? | Target Latency |
|---|---|---|---|
| Browse / search questions | Free | No | <50ms |
| Answer a 1–2 mark question | Free | No (Deterministic DSL) | <10ms |
| Answer a 3+ mark question | Free | Yes (1.5B–3B self-hosted model) | <300ms |
| Generate a custom paper | Free | Yes | <1s |
| Explain this to me | Free | Yes | <300ms |
| Weakness report | Free | No (Precomputed scores) | <20ms |

---

## 3. Core Application Features

### 3.1 Past paper ingestion pipeline

The central cost- and quality-control insight: expensive AI work happens once, at ingestion time, not on every student interaction.

1. Admin uploads a past paper PDF (question paper + mark scheme).
2. **Dual extraction paths for visual content**:
   - PyMuPDF's embedded-image extraction (`get_images()`) catches raster images (photos, scanned diagrams) cleanly with bounding-box coordinates.
   - **Vector fallback**: Many exam-board diagrams (circuit diagrams, graphs, geometric figures) are drawn as vector paths in the PDF page stream rather than embedded images. If boundary detection flags a region as containing visual content but no embedded image object overlaps that bounding box, the pipeline falls back to rasterising that page region directly at 2–4x resolution and cropping it.
3. pdfplumber handles table extraction, returned as clean 2D arrays.
4. Questions are split by boundary detection (regex + heuristics, AI only for edge cases).
5. **Shared images across sub-questions**: A diagram in the stem of a question (e.g. above "3(a)") is often required for sub-questions "3(b)(i)" and "3(b)(ii)". The assignment step detects images whose bounding box sits above a shared stem (before the first sub-question boundary) and links them to all sub-questions under that stem.
6. **Image validation & storage guarantees (fail-loud design)**:
   - *Sanity check*: Every extracted image undergoes a cheap automated check for minimum dimensions and non-uniform color (detecting empty crops). Fails check OR boundary detection flagged a diagram but extraction yielded nothing usable → question is auto-flagged `needs_review` and blocked from auto-publishing.
   - *Permanent source*: Original source PDFs are retained permanently (`papers.source_pdf_url`) so images can be re-cropped if a bug is discovered later.
   - *Integrity*: Uploaded to Cloudflare R2 with content-addressed keys (byte hash). The SHA-256 checksum is stored in `questions.images` JSONB alongside URL, bounding box, and text description to detect corruption on read.
7. Each question is classified by mark value.
8. Specification-point references printed in mark schemes (e.g. "4.2.1 Cell division") are parsed directly — no AI needed for topic categorisation.
9. **Admin review UI**: Displays the original PDF page with detected bounding boxes overlaid next to cropped results, enabling single-action re-cropping or re-assignment.

**Marking models produced at ingestion:**

- **1–2 mark questions** → deterministic DSL, no model call required at runtime.
- **3+ mark questions** → raw mark scheme text + image text descriptions (§3.1a) stored as context for a self-hosted marking model call at runtime (§6). No hosted API (Claude/Gemini) is used for marking, ever.

**Deterministic marking DSL operators:**

| Operator | Example | Description |
|---|---|---|
| `MCQ` | `MCQ:OPTIONA` | Multiple choice — exact option match |
| `CONTAIN` | `CONTAIN:mitosis` | Answer must include this term |
| `NOT CONTAIN` | `NOT CONTAIN:meiosis` | Answer must not include this term |
| `ANY` | `ANY:glucose,sugar,C6H12O6` | Any one of these synonyms accepted |
| `ALL` | `ALL:glucose,water,oxygen` | All terms must be present |
| `EXACT` | `EXACT:4.5` | Exact value match |
| `RANGE` | `RANGE:4.2,4.8` | Numerical answer within tolerance |
| `REGEX` | `REGEX:[0-9]+\s*cm` | Pattern match (units, formats) |

Operators combine with AND / OR logic. For 3+ mark questions, the self-hosted marking model call returns structured JSON (marks awarded, feedback text, missed marking points, and misconception tags — §3.7) that feeds directly into the weakness tracker and the per-student mistake memory.

### 3.1a Image description generation for marking context

How does a text-based self-hosted marking model "see" diagrams?

- **Option A (Vision-capable marking models)**: Fine-tune vision-language base models (e.g. Qwen2-VL, Llama 3.2 Vision) per spec code. *Drawback*: Requires complex vision LoRA tooling and expensive GPU memory per inference call across dozens of spec codes.
- **Option B (Text descriptions generated once at ingestion — Acexam default)**: At ingestion time (where hosted-API calls are already budgeted as a one-off cost), a vision-capable hosted API scans each extracted image and generates a detailed, structured text description (axis labels, key data points, diagram components, trend directions). This description is stored in `questions.images` (`[{"url":..., "bbox":..., "checksum":..., "description": "..."}]`).

Option B keeps the entire self-hosted serving stack 100% text-only, saving significant GPU memory and operational complexity across spec-code adapters. Option A remains documented as a potential future upgrade path.

**Ground-truth validation**: To prevent a bad hosted-API description from becoming permanent flawed ground truth for all future marking, each image description is generated twice independently via hosted API. Disagreements in key details automatically route the image to admin review before publishing.

### 3.2 Master Student Strength & Weakness Profile Engine

Rather than maintaining a basic topic score, Acexam constructs a multi-dimensional, real-time **Master Student Profile** for each user per spec code. This profile acts as the central source of truth for student competence and **informs every product feature across the entire application** at zero additional runtime AI cost.

#### 1. Dimensions Tracked in the Profile:
- **Specification Topic Mastery Tree** (`student_topic_mastery`): Quantitative mastery score (0%–100%) per spec point (e.g. `4.2.1 Cell division`), calculated using an Exponentially Weighted Moving Average (EWMA) to prioritize recent attempts over historical ones.
- **Ebbinghaus Memory Decay Curve**: Mastery scores gradually decay over time if unpracticed ($M(t) = M_0 \cdot e^{-t/S}$), automatically flagging topics due for spaced-repetition revision before knowledge vanishes.
- **Command Word Competency Matrix** (`student_command_word_mastery`): Slices performance by exam command words (e.g. 95% accuracy on *State / Describe*, 40% on *Evaluate / Explain*).
- **Persistent Misconception Memory** (`student_misconceptions` — §3.7): Tracks active misconception tags from the canonical taxonomy (§6.2a), recurrence frequency, and resolution status (marked resolved after 3 consecutive correct applications).
- **Execution & Technical Penalty Markers**: Tracks recurring non-conceptual errors, such as missing unit labels, incorrect decimal precision / significant figures, or skipped chemical balancing steps.

#### 2. How the Profile Informs Everything in Acexam:
- **Adaptive Practice Queue (§3.5)**: Automatically assembles practice sessions weighted by decaying memory scores, unresolved misconceptions, and weak command words.
- **Custom Paper Generation (§3.3)**: Tailors generated paper difficulty, topic weighting, and question types to target the student's exact grade-boundary vulnerabilities.
- **Personalized AI Explanations ("Explain This To Me")**: Adjusts explanation depth and analogies based on prerequisite topics the student has mastered vs. active misconceptions they currently hold.
- **Targeted Question Variants (§3.6)**: Synthesizes variants specifically targeting the command words or execution traps where the student's profile indicates high error rates.
- **Visual Mastery Dashboard**: Displays an interactive specification tree heatmap (Green / Amber / Red), active misconception hit lists, and predicted grade boundary trajectories.

### 3.3 Custom paper generation (Profile-Informed)

Students can assemble custom mock papers filtered by subject, board, topic, and mark range. By default, the paper generator consults the student's **Master Profile (§3.2)** to automatically balance topic distribution and question difficulty toward their specific grade-boundary gaps. Papers are assembled from the ingested question bank wherever possible (fast, free, accurate); AI is invoked only for "free-form" requests not covered by the bank.

### 3.4 Photo-based answer capture & marking

Students can photograph or upload an image of a handwritten answer instead of typing it. OCR extracts the text from the image, which then flows through the *existing* marking pipeline unchanged — deterministic DSL for 1–2 mark answers where the extracted text permits it, AI marking for 3+ mark answers. This reuses the marking infrastructure rather than building a parallel path, so it inherits the same mark-scheme accuracy as typed answers.

**Explicit Scope Boundary**: This feature covers marking typed or handwritten text answers submitted via photos (questions that reference diagrams in the paper stem are supported via §3.1a image descriptions). It **does not cover** marking a student's own hand-drawn diagram as their answer (e.g. "sketch the graph of velocity against time") — that is a genuinely harder vision problem on the answer side and is explicitly out of scope for v1.

### 3.5 Adaptive practice queue (Profile-Driven)

Opening "Practice" launches an automated revision session assembled directly by the **Master Profile Engine (§3.2)**. Sessions dynamically interleave:
1. **Active Misconception Tags** (highest priority): Questions specifically tagged with misconceptions the student has recently triggered.
2. **Decaying Spec Topics** (spaced repetition): Topics whose memory decay curves indicate knowledge is about to fade.
3. **Weak Command Words**: Questions utilizing command words where the student's competency matrix shows low accuracy (e.g. *Evaluate*).

This removes the need for manual filtering or spec knowledge, delivering an auto-targeted practice loop tailored to the individual.

### 3.6 Fresh question variants (Profile-Targeted)

To prevent past-paper memorisation, AI generates variants of existing bank questions during idle/batch processing time. The **Master Profile Engine (§3.2)** guides variant generation priorities — prioritizing question variants with command words, contexts, or calculation steps where student profile data indicates widespread confusion. Each variant is validated against the source question's DSL or mark scheme before entering the pool, preserving accuracy guarantees.

### 3.7 Persistent per-student misconception memory

This component of the **Master Profile (§3.2)** tracks specific conceptual errors across attempts. Every time the self-hosted model marks a 3+ mark answer, its output includes misconception tags drawn from the canonical taxonomy (§6.2a):

- **Targeted Practice**: Active misconceptions are prioritized at the top of the adaptive practice queue (§3.5).
- **Contextual Feedback**: Marking feedback references historical patterns ("This is the 3rd time you've confused mitosis with meiosis").
- **Resolution Tracking**: A misconception tag transitions to `resolved` once a student answers 3 consecutive questions touching that tag correctly, preventing the queue from over-indexing on fixed mistakes.

### 3.8 Interactive Exam Paper Canvas UI

Inspired by Medly.ai's canvas workspace, Acexam presents questions inside an authentic **Exam Paper Canvas UI** designed to replicate the look and feel of real exam board question papers (AQA, Edexcel, OCR) while providing digital inking and instant visual marking feedback.

1. **Authentic Paper Styling & Layout**:
   - Renders questions in true exam-board typography (official serif/sans-serif fonts, bold question identifiers like `3(b)(ii)`, mark indicators `[3 marks]`, and embedded vector/raster diagrams).
   - Lined answer zones with line counts matched to the mark value.
2. **Dual-Mode Input Workspace (Typed & Drawn)**:
   - **Typed Response**: Direct keyboard entry into lined text fields.
   - **HTML5 Canvas / SVG Overlay**: A vector drawing overlay powered by a client-side drawing engine (e.g., Fabric.js / Perfect Freehand).
   - **Canvas Tools**: Pen (with thickness/color controls & stylus pressure sensitivity), Highlighter, Eraser, Clear, Undo/Redo, Zoom, and Pan.
   - Enables students to sketch graphs, write out mathematical working, balance equations, or annotate diagrams directly on the paper.
3. **Instant Canvas OCR Submission**:
   - Inked strokes are rendered to image bytes client-side upon clicking "Submit".
   - Handwriting is extracted via OCR (§3.4) and marked by the self-hosted ultra-lightweight model (§6) in **<300ms**.
4. **Inline Visual Feedback & Annotations**:
   - Marking results are overlaid **directly onto the paper canvas**:
     - **Green Ticks ($\checkmark$)** and **Red Crosses ($\times$)** rendered alongside student answer lines.
     - **Margin Popover Chips**: Clickable tags highlighting missed marking points or recognized misconceptions (§3.7) anchored to the exact line where the error occurred.
     - **Official Mark Scheme Toggle**: One-click inline toggle to reveal the official mark scheme text and guidance directly underneath the student's attempt.

---

## 4. Application Architecture

| Layer | Technology | Notes |
|---|---|---|
| Frontend | React | Talks to backend via `fetch()` / API client |
| Backend | Python FastAPI | Async endpoints; routers: `/auth`, `/exams`, `/generate`, `/feedback`, `/analytics` |
| Task queue | ARQ + Redis | Async paper ingestion and generation jobs |
| Primary database | PostgreSQL | Users, papers, questions, attempts, answers, weakness_scores, misconception_taxonomy |
| Cache / rate-limit | Redis | Session tokens, sliding-window rate limits (60 req/min) |
| Object storage | Cloudflare R2 | Extracted question images (no egress fees) |
| Auth | JWT + OAuth | Google login + email/password; JWT in httpOnly cookies |
| Ingestion AI | Hosted API (abstracted) | Claude and/or Gemini for offline/admin work only: boundary edge cases, synthetic data & auto-grading (§6.2), and sampled auditing (§6.3). **Never in the path of a live student mark.** |
| Model serving | Self-hosted vLLM engine | Serves promoted **per-spec-code LoRA adapters** on ultra-lightweight base models (1.5B–3B parameter class). Delivers **sub-300ms near-instant feedback** for all students. |
| Model training | Offline LoRA fine-tuning pipeline | Ephemeral batch jobs; pre-trains checkpoints per spec code directly from ingestion-sourced mark schemes, exemplar answers, and examiner reports (§6.2). |

### 4.1 Rate limiting & concurrency protection

With zero paywalls and 100% free access for all students, system protection focuses on rate limiting and bot mitigation:

- **Sliding-window rate limits**: Enforced via Redis (`rate_limit:{user_id}`) at 60 requests/minute to prevent automated scraping or denial-of-service botting.
- **Near-instant inference queueing**: Because 1.5B–3B parameter models process tokens at >1,000 tokens/sec, request queues clear almost instantaneously without needing complex priority tiering.
- **No token metering or payment ledgers**: Billed per GPU-hour as a low fixed infrastructure cost, with no per-user payments or token ledgers required.

---

## 5. AI Cost Model & Speed Optimization

Self-hosting **ultra-lightweight models (1.5B–3B parameters)** achieves two critical goals simultaneously: **100% free core access** and **near-instant sub-300ms feedback**:

- **Ultra-Fast Generation Speed**: A 1.5B–3B model (e.g. Qwen2.5-1.5B or Llama-3.2-3B) outputs tokens at **1,000–1,500 tokens/second** on standard GPU hardware. Generating a 150-token structured evaluation JSON takes **~100–150ms**.
- **Prefix Caching**: vLLM's Automatic Prefix Caching caches common system prompts, question text, and mark schemes in VRAM, dropping prompt prefill latency to **<20ms**. Total end-to-end user latency is **<300ms**.
- **Minimal VRAM Footprint**: A 1.5B model in FP8 requires **<1.5 GB VRAM** (a 3B model requires **<3 GB VRAM**). Multiple base models and dozens of spec-code LoRA adapters run simultaneously on a single inexpensive GPU (e.g., NVIDIA T4, RTX 4060/4090, or L4).
- **Offline Training**: Fine-tuning a 1.5B–3B model adapter using QLoRA takes **<10 minutes** on a rented A100/RTX 4090, costing **<$0.20 per retrain job**.
- **Net effect**: Acexam operates with near-zero marginal cost per student while delivering an instant, responsive UI experience.

---

## 6. Marking Model Training Strategy

There is no hosted-API marking phase, interim or otherwise. Marking is done exclusively by a dedicated, self-hosted model trained **per spec code** — e.g. a distinct model for AQA 8462/H (GCSE Combined Science Trilogy, Higher), a separate one for AQA 8461 (GCSE Biology), a separate one for Edexcel's equivalent, and so on — and each model is **pre-trained from ingestion data**: mark schemes, exemplar answers, and examiner reports harvested while past papers for that spec code are processed (§3.1), not from live student traffic. This mirrors the mechanism that appears to let Revise2 run free at scale: narrow, cheap, self-hosted models handling all of marking, rather than a single general-purpose model billed per token. The practical consequence is that a spec code's *AI* marking (3+ mark questions) doesn't go live until its own model has been trained and gated — 1–2 mark questions are unaffected, since those are answered by the deterministic DSL from the moment a paper is ingested, with no dependency on any model.

### 6.1 Why per spec code, not per subject or per board

A spec code (e.g. `8462/H`) is the actual unit that determines what a mark scheme rewards — command words, level-of-response criteria, and acceptable answer patterns differ between tiers (Foundation vs Higher) and between specifications even within the same subject and board. Training at this granularity means:

- Each model only ever needs to reason about one, internally consistent marking scheme, not generalise across specs with different conventions.
- Quality can be evaluated and gated independently per spec code — a strong model for AQA Biology Higher doesn't need to wait on a weaker one for Edexcel Physics Foundation.
- New spec codes can be onboarded incrementally as ingestion coverage for that spec grows, without retraining anything already live.

### 6.2 Where the training data comes from

The ingestion pipeline (§3.1) is the primary and starting source — a spec code can be pre-trained and gated *before* it has a single live student, and the labeling itself is automated so it scales with ingestion volume rather than admin bandwidth:

- **Seed data (bootstraps every spec code)**: mark schemes, exemplar/model answers printed within them, and (where published) examiner reports, for every past paper processed for that spec code.
- **Misconception taxonomy generation (§6.2a)**: Runs *before* any synthetic training data is generated, establishing a canonical, admin-approved taxonomy of misconception tags per spec code.
- **Synthetic answer generation**: a hosted API generates candidate answers targeting specific mark levels for a question (e.g. "an answer that scores 2/4 by getting the first point but omitting the balancing step"), conditioned on that question's real mark scheme and image descriptions (§3.1a). This is what turns a mark scheme — which only shows the ideal answer — into the full spread of full/partial/zero-mark answers a marking model actually needs to learn from.
- **Independent auto-grading**: a *separate* hosted API call, blind to the level it was asked to target, marks that synthetic answer against the same mark scheme and produces the full structured label (marks, feedback, missed points, misconception tags drawn from the approved §6.2a taxonomy).
- **Automatic cross-check, no human in the loop**: if the independent grade agrees with the intended generation level, the example is accepted into the training set automatically. Disagreement discards or regenerates it. This is what makes the pipeline scale with ingestion volume rather than admin review capacity — accept/reject is a machine comparison, not a queued task for a person.
- **Admin role narrows to calibration auditing, not per-example review**: a small, fixed-size sample of accepted examples (e.g. a weekly batch, not a percentage that grows with ingestion volume) goes to admin for a spot-check. This catches systematic issues — the hosted grader consistently misreading a spec's conventions — that a self-consistent cross-check can't catch on its own, since a generator and grader agreeing with each other doesn't guarantee they're both right.
- **From production, once a spec code's model is live**: every live-marked answer is a candidate for the same audited pipeline (§6.3) — the async hosted-API judge samples served marks, and disagreements feed retraining the same way synthetic-example rejections do.

### 6.2a Misconception taxonomy timing and lifecycle

When is the misconception taxonomy generated?

It **must exist before** the synthetic answer generation and auto-grading step in §6.2, not alongside or after it — the independent auto-grader outputs `misconception_tags`, and if there is no fixed vocabulary yet, every grading call invents its own phrasing ("mixes up mitosis and meiosis" vs "confuses meiosis/mitosis"). That breaks both the automatic cross-check (nothing to compare against) and the per-student misconception memory in §3.7 (which needs a stable tag to detect recurrence — free text cannot be matched reliably across attempts).

So taxonomy generation is its own dedicated step, sitting right after a spec code's mark schemes and examiner reports are ingested, before synthetic training data is generated:

1. **Ingestion & Scanning**: Examiner reports describe common candidate errors ("many candidates confused momentum with force"). Once a spec code has ingested examiner reports, a hosted-API call scans all of them for that spec code and proposes a canonical, deduplicated list of misconception tags — fixed `snake_case` identifiers, each with a short label and description.
2. **Admin Review & Approval**: Unlike synthetic training answers (thousands of examples needing automated cross-checks), the taxonomy itself is small — dozens of tags per spec code, not thousands. Full admin review is feasible and critical: this is foundational reference data that every future synthetic example and every future misconception-memory record depends on. A wrong tag here propagates everywhere downstream.
3. **Append-Only Lifecycle**: The taxonomy is not static — it grows as more paper series and examiner reports are ingested for a spec code over time. New candidate tags are proposed the same way, checked for semantic overlap against existing tags (to avoid near-duplicates), and pass through lightweight admin approval (`approved_at`) before being added — an append-only, versioned list per spec code.
4. **Schema Object**: Backed by a dedicated `misconception_taxonomy` table (`spec_code`, `tag_id`, `label`, `description`, `approved_at`) rather than leaving tags as loose, unvalidated strings in answers.

### 6.3 Rollout gating — no spec code goes live untested

Given how much trust the product's differentiation depends on ("mark-scheme-accurate marking"), a bad fine-tune is a trust-destroying bug, not a cosmetic one. Because there's no hosted API in the live path to compare against, gating leans on the automated pipeline above pre-launch and on sampled, asynchronous auditing during a spec code's early live period — not on a human reviewing every mark, and not on a shadow-mode comparison against another live AI marker:

1. **Pre-training** — fine-tune (LoRA-style, on an open-weight base model) using that spec code's ingestion-sourced and synthetic-plus-cross-checked training data (§6.2). Happens as soon as a spec code has enough seed coverage — can run well ahead of Phase 4, during ingestion itself.
2. **Held-out evaluation** — score the model against a held-out slice of that same training data (a full paper series excluded from training, not a random split), plus any real admin-audited answers already available; require a minimum agreement rate before proceeding. A spec code that doesn't clear this bar simply isn't offered AI marking yet — its 3+ mark questions stay flagged `needs_review`/unavailable for AI marking until retrained.
3. **Audited launch** — the model becomes the *only* marker for that spec code's live 3+ mark answers, shown to the student immediately with no review gate on latency. In the background, a fixed-rate sample of already-served marks (not all of them) is sent to a hosted API for an independent second opinion; disagreements above a threshold are queued for admin review, and every audited example — agreement or not — feeds the next retrain. This scales with a sampling rate, not with traffic.
4. **Full autonomy** — once the sampled agreement rate holds above threshold over a meaningful sample size, the audit sampling rate can be turned down (not off — ongoing monitoring never fully stops) rather than requiring any change to what students see.
5. **Ongoing monitoring** — the audit sample never disappears entirely; if the live agreement rate drops, the sampling rate is turned back up and the spec code can be pulled back toward stage 3 behaviour (heavier audit, faster retrain cadence) until a retrain restores it. There's no hosted-API fallback to fail over to — the response to degraded trust is more auditing and retraining, not rerouting live marks elsewhere.

### 6.4 Interim and steady state

A spec code has no AI marking at all until it passes stage 2 above — there is no default "everyone starts on X" marker to fall back to, since the whole point is removing any hosted-API dependency from the marking path. In practice this means the initial spec-code coverage for Phase 4 launch should be chosen deliberately (the highest-ingestion-volume, most-requested specs first), and rollout is staged per spec code rather than a single cutover date. A spec code with sustained low ingestion volume may simply sit in "DSL-only" (1–2 mark questions available, 3+ mark AI marking not yet offered) indefinitely, rather than degrading to a lower-trust hosted-API path — this is a deliberate trade of coverage breadth for the "never calls a hosted API for marking" guarantee.

### 6.5 Training pipeline mechanics

**Training example format.** Each labelled example (synthetic-and-cross-checked, or audited-production) is stored as an instruction/response pair matching the exact shape used at inference time, so training and serving never diverge:

```
System:  "You are marking a GCSE [spec code] exam answer. Given the question, mark
          scheme, and mark value, output JSON with marks_awarded, feedback_text,
          missed_points, and misconception_tags."
User:    question text + mark scheme + mark value + candidate answer
Assistant: {"marks_awarded": 2, "feedback_text": "...", "missed_points": [...],
            "misconception_tags": ["confuses_mitosis_meiosis"]}
```

Stored as JSONL per spec code. Split for held-out evaluation **by paper series, not randomly** — e.g. train on 2019–2023 series, hold out 2024 entirely — so evaluation measures generalisation to an unseen series rather than memorisation of a shuffled subset.

**Base model and method.** An ultra-lightweight open-weight instruction-tuned model in the **1.5B–3B parameter range** (e.g. Qwen2.5-1.5B / 3B or Llama 3.2 3B) is used as the shared base model. Because exam marking is a constrained pattern-matching task against a explicit mark scheme, a 1.5B–3B base model fine-tuned on spec-code data achieves high marking precision while generating tokens at >1,000 tokens/sec for near-instant <300ms feedback. Fine-tuning uses QLoRA (4-bit quantized base + LoRA adapters) on a single GPU. Typical settings: LoRA rank 16–32, alpha = 2× rank, dropout 0.05.

**Tooling.** Axolotl or Unsloth on top of Hugging Face's `transformers` / `peft` / `trl` stack. Config-driven (YAML per run: base model, dataset path, LoRA settings, learning rate, epochs) rather than a bespoke script per spec code, since the same recipe is rerun dozens of times with only the dataset changing. Structured output is enforced at inference time via grammar-constrained/JSON-mode decoding on the serving side, so the model can't drift outside the JSON schema even on an imperfect fine-tune.

**Hyperparameters (starting point, tuned per spec code as needed).** LR ~1e-4–2e-4 for LoRA; 2–3 epochs — the task is narrow and repetitive (same JSON schema every time) so it converges fast, and more epochs risks overfitting to the synthetic generator's own quirks rather than learning real marking judgement; batch size driven by GPU memory with gradient accumulation to an effective batch of ~16–32. Epochs/LR are the two worth actually sweeping per spec code, since a spec code with 200 examples needs different treatment than one with 5,000 — cheap to grid given how short each run is.

**Evaluation loop.** After each candidate checkpoint: exact-match rate on `marks_awarded` against the held-out label, plus a looser overlap score for `missed_points`/`misconception_tags` (embedding similarity or keyword overlap, not exact string match, since tagging is more subjective than mark counting). Only checkpoints clearing the §6.3 stage-2 threshold become launch candidates. Every checkpoint's eval score is logged against a version ID, building the retrain history a spec code accumulates over time.

**Versioning and serving.** Each spec code's LoRA adapter is version-tagged (e.g. `aqa-8462h-v3`), never overwritten in place, so a regression can be rolled back instantly. vLLM serves one shared base model with many LoRA adapters loaded simultaneously, routing each request to the right adapter by spec code — this is what keeps "one base model, dozens of spec-code adapters" cheap to serve rather than needing a dedicated model instance per spec code.

**Retraining cadence.** Batched, not continuous: retrain a spec code when either enough new audited production examples have accumulated (e.g. 200+) or a scheduled monthly job fires, whichever comes first. Every retrain reruns the full held-out eval gate before its checkpoint can replace the live adapter — an unevaluated checkpoint never gets hot-swapped into production.

---

## 7. Example Database Schemas

These are illustrative starting schemas — field types and constraints to refine during Phase 1, not a final migration.

```sql
-- ============================================================
-- USERS & AUTH
-- ============================================================

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT,                     -- NULL if OAuth-only
    oauth_provider  TEXT,                      -- 'google', NULL for email/password
    oauth_subject   TEXT,                      -- provider's user id
    display_name    TEXT,
    exam_board      TEXT,                      -- e.g. 'AQA', 'Edexcel', 'OCR'
    year_group      TEXT,                      -- e.g. 'GCSE', 'A-Level Y13'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);

CREATE UNIQUE INDEX idx_users_oauth ON users (oauth_provider, oauth_subject)
    WHERE oauth_provider IS NOT NULL;


-- ============================================================
-- SPECIFICATION TOPICS (parsed reference tree, e.g. AQA Biology)
-- ============================================================

CREATE TABLE spec_topics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_board      TEXT NOT NULL,             -- 'AQA'
    subject         TEXT NOT NULL,             -- 'Biology'
    spec_code       TEXT NOT NULL,             -- '4.2.1'
    title           TEXT NOT NULL,             -- 'Cell division'
    parent_id       UUID REFERENCES spec_topics(id)
);

CREATE UNIQUE INDEX idx_spec_topics_code ON spec_topics (exam_board, subject, spec_code);


-- ============================================================
-- MISCONCEPTION TAXONOMY (canonical tags per spec code)
-- ============================================================

CREATE TABLE misconception_taxonomy (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    spec_code       TEXT NOT NULL,              -- e.g. '8462/H' or '4.2.1'
    tag_id          TEXT NOT NULL,              -- e.g. 'confuses_mitosis_meiosis'
    label           TEXT NOT NULL,              -- 'Confuses mitosis and meiosis'
    description     TEXT NOT NULL,              -- 'Mistakes process steps or cell outcome between mitosis and meiosis'
    approved_at     TIMESTAMPTZ,                -- NULL if pending admin approval
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_misconception_tax_spec_tag ON misconception_taxonomy (spec_code, tag_id);


-- ============================================================
-- PAPERS & QUESTIONS (ingestion output)
-- ============================================================

CREATE TABLE papers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_board      TEXT NOT NULL,
    subject         TEXT NOT NULL,
    paper_code      TEXT,                      -- e.g. '8461/1H'
    series          TEXT,                      -- e.g. 'June 2023'
    source_pdf_url  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'processing',
                    -- 'processing' | 'needs_review' | 'published' | 'failed'
    uploaded_by     UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE questions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id            UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    question_number     TEXT NOT NULL,          -- '3(b)(ii)'
    mark_value          SMALLINT NOT NULL,
    question_text       TEXT NOT NULL,
    images              JSONB NOT NULL DEFAULT '[]',
                        -- [{ "url": "...", "bbox": [x0,y0,x1,y1], "checksum": "...", "description": "..." }, ...]
    table_data          JSONB,                 -- pdfplumber-extracted tables, if any
    spec_topic_id        UUID REFERENCES spec_topics(id),
    marking_type        TEXT NOT NULL,          -- 'dsl' | 'ai'
    marking_dsl         TEXT,                   -- e.g. 'ANY:glucose,sugar AND CONTAIN:respiration'
    mark_scheme_text    TEXT,                   -- raw text, used for AI marking (3+ mark questions)
    needs_review        BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_questions_paper ON questions (paper_id);
CREATE INDEX idx_questions_spec_topic ON questions (spec_topic_id);
CREATE INDEX idx_questions_filter ON questions (mark_value, marking_type);


-- ============================================================
-- ATTEMPTS & ANSWERS (student activity)
-- ============================================================

CREATE TABLE attempts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    paper_id        UUID REFERENCES papers(id),   -- NULL if a custom/generated paper
    source          TEXT NOT NULL DEFAULT 'bank', -- 'bank' | 'custom_generated'
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE TABLE answers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id          UUID NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    question_id         UUID NOT NULL REFERENCES questions(id),
    user_id             UUID NOT NULL REFERENCES users(id),
    answer_text         TEXT,
    answer_image_url    TEXT,                  -- set when the answer was submitted as a photo
    ocr_text            TEXT,                  -- OCR output from answer_image_url, feeds the same marking pipeline as answer_text
    marks_awarded       SMALLINT,
    marks_possible      SMALLINT NOT NULL,
    feedback_text       TEXT,                  -- populated for AI-marked answers
    missed_points       JSONB,                  -- ["did not mention active transport", ...]
    marked_by           TEXT NOT NULL,          -- 'dsl' | 'ai'
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_answers_attempt ON answers (attempt_id);
CREATE INDEX idx_answers_user_question ON answers (user_id, question_id);
-- Powers the weakness tracker: aggregate marks_awarded/possible per spec_topic_id via questions join.


-- ============================================================
-- MASTER STUDENT PROFILE TABLES (§3.2)
-- ============================================================

-- 1. Topic Mastery & Memory Decay Tree
CREATE TABLE student_topic_mastery (
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    spec_topic_id       UUID NOT NULL REFERENCES spec_topics(id),
    mastery_score       FLOAT NOT NULL DEFAULT 0.0,   -- EWMA score (0.0 to 1.0)
    attempts_count      INTEGER NOT NULL DEFAULT 0,
    last_practiced_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decay_score         FLOAT NOT NULL DEFAULT 0.0,   -- Ebbinghaus decay score, recalculated on queue load
    PRIMARY KEY (user_id, spec_topic_id)
);

CREATE INDEX idx_student_topic_decay ON student_topic_mastery (user_id, decay_score ASC);

-- 2. Command Word Competency Matrix
CREATE TABLE student_command_word_mastery (
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    command_word        TEXT NOT NULL,                -- e.g. 'Evaluate', 'Describe', 'Calculate'
    marks_awarded       INTEGER NOT NULL DEFAULT 0,
    marks_possible      INTEGER NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, command_word)
);

-- 3. Persistent Misconception Memory (§3.7)
CREATE TABLE student_misconceptions (
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    spec_code           TEXT NOT NULL,
    tag_id              TEXT NOT NULL,                -- references misconception_taxonomy(tag_id)
    occurrences         INTEGER NOT NULL DEFAULT 1,
    consecutive_correct SMALLINT NOT NULL DEFAULT 0,  -- 3 in a row transitions status to 'resolved'
    status              TEXT NOT NULL DEFAULT 'active',-- 'active' | 'resolved'
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, spec_code, tag_id)
);

CREATE INDEX idx_student_misconceptions_active ON student_misconceptions (user_id, status)
    WHERE status = 'active';


-- ============================================================
-- QUESTION VARIANTS (AI-generated freshness layer over the ingested bank)
-- ============================================================

CREATE TABLE question_variants (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_question_id  UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    variant_text        TEXT NOT NULL,
    images              JSONB NOT NULL DEFAULT '[]',
    marking_type        TEXT NOT NULL,          -- inherited from source_question, re-validated at generation time
    marking_dsl         TEXT,
    mark_scheme_text    TEXT,
    validated           BOOLEAN NOT NULL DEFAULT false,
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_question_variants_source ON question_variants (source_question_id);
-- Only variants with validated = true are eligible for paper generation / practice queue serving.
```

---

## 8. Multi-Stage Build Roadmap

The build is sequenced so every stage ships a usable increment, with the highest-leverage cost-saving feature (ingestion) built before the highest-cost feature (live AI marking).

### Phase 1 — Foundation
*Goal: a working application skeleton the rest of the product can be built on.*

- [ ] FastAPI skeleton with routers stubbed out (`/auth`, `/exams`, `/generate`, `/feedback`, `/analytics`)
- [ ] Postgres schema: `users`, `spec_topics`
- [ ] Redis wired up for sessions and rate-limit keys
- [ ] React frontend shell with routing and API client
- [ ] Auth flow: email/password + Google OAuth, JWT in httpOnly cookies

### Phase 2 — Ingestion Pipeline
*Goal: turn a raw past-paper PDF into structured, markable questions.*

- [ ] Postgres schema: `papers`, `questions`, `misconception_taxonomy`
- [ ] PDF upload flow for admins
- [ ] PyMuPDF dual image extraction: embedded raster (`get_images()`) + 2–4x page-region rasterisation fallback for vector paths
- [ ] Image sanity validation (dimensions + non-blank color check) → auto-flag `needs_review` on failure
- [ ] pdfplumber table extraction
- [ ] Question boundary detection (regex/heuristics, AI fallback for edge cases)
- [ ] Stem image assignment shared across sub-questions (e.g. 3(a), 3(b)(i), 3(b)(ii))
- [ ] Dual independent image-description generation via hosted vision API (§3.1a), with disagreement routing to admin review
- [ ] Upload extracted images to object storage (Cloudflare R2) with content-addressed keys and checksum verification
- [ ] Spec-point parsing from mark schemes → populate `spec_topics` links
- [ ] Deterministic DSL generation for 1–2 mark questions
- [ ] Examiner report ingestion & initial misconception tag proposal (§6.2a)
- [ ] Admin review UI displaying PDF page bounding-box overlays next to cropped image results for 1-action re-cropping/re-assignment

> Note: as soon as a spec code has meaningful ingested coverage here, its per-spec-code marking model (§6) can start pre-training in parallel — it doesn't need to wait for Phase 4. Treat model pre-training as a parallel track fed by this phase's output, not a strictly later phase.

### Phase 3 — Student Product Core
*Goal: students can browse, answer, and get instant feedback on an authentic exam-paper canvas workspace powered by the Master Profile Engine.*

- [ ] Postgres schema: `attempts`, `answers`, `student_topic_mastery`, `student_command_word_mastery`, `student_misconceptions`
- [ ] **Master Student Profile Engine (§3.2)**: Real-time EWMA topic mastery calculation, Ebbinghaus memory decay curves, and command word competency matrix
- [ ] Question browser with subject/board/topic filtering
- [ ] **Exam Paper Canvas UI (§3.8)**: Authentic exam-board typography, question layout, and lined answer zones
- [ ] **HTML5 Canvas / SVG Drawing Overlay**: Vector pen, highlighter, eraser, stylus pressure sensitivity, undo/redo, zoom/pan tools
- [ ] Dual input handling (typed text + client-side ink rasterisation for OCR submission)
- [ ] Deterministic DSL marking engine (zero AI cost path)
- [ ] Inline visual marking annotations (ticks/crosses, margin misconception chips, mark scheme toggle)
- [ ] Profile-driven adaptive practice queue (§3.5): session assembly dynamically interleaving active misconceptions, decaying topics, and weak command words
- [ ] Visual Student Dashboard: Specification tree heatmap (Green/Amber/Red), active misconception hit list, and target grade trajectory projection

### Phase 4 — AI Marking Layer (Ultra-Fast & Free)
*Goal: extend marking to free-response 3+ mark questions via self-hosted, ultra-lightweight (1.5B–3B) spec-code models with <300ms feedback latency.*

**Training data pipeline (can start as soon as Phase 2 has coverage for a spec code — see note above):**
- [ ] Misconception taxonomy review & approval (§6.2a): admin approves fixed `snake_case` tags per spec code *before* synthetic generation begins
- [ ] Synthetic training-answer generation: hosted-API calls that write candidate answers targeting specific mark levels, conditioned on each ingested question's mark scheme and image descriptions (§3.1a)
- [ ] Independent auto-grading: a separate hosted-API call marks each synthetic answer blind to its intended level, producing the structured label (marks, feedback, missed points, misconception tags from approved taxonomy)
- [ ] Automatic cross-check: accept into the training set on agreement, discard/regenerate on disagreement — no per-example human step
- [ ] Fixed-size (not volume-scaling) admin calibration-audit sample, to catch systematic grader drift

**Training infrastructure:**
- [ ] Training-set assembly into JSONL instruction/response pairs, split by paper series for held-out evaluation (§6.5)
- [ ] Offline QLoRA fine-tuning pipeline (Axolotl or Unsloth) for 1.5B–3B parameter base models, producing versioned adapter checkpoints
- [ ] Held-out evaluation harness (§6.3 stage 2 / §6.5) — exact-match on marks, overlap scoring on missed points/misconception tags; gates a spec code before it's eligible for live marking
- [ ] Self-hosted inference service (vLLM) with prefix caching for sub-300ms response times across shared 1.5B–3B base models
- [ ] Async audit job (§6.3 stage 3): samples already-served live marks at a fixed rate, sends to a hosted API for an independent second opinion, queues disagreements for admin review, feeds results back into the next retrain
- [ ] Scheduled/threshold-triggered retrain job per spec code (new audited-example count or monthly, whichever first), always regated through held-out eval before replacing the live adapter

**Live marking:**
- [ ] Live marking call for 3+ mark questions, routed to that question's spec-code model → structured JSON (marks, feedback, missed points, misconception tags — §3.7)
- [ ] Marking blocked (falls back to `needs_review`, no AI mark shown) for any spec code without a gated model — never routed to a hosted API
- [ ] Missed marking points feed into the weakness tracker
- [ ] Misconception tags feed the per-student mistake memory (§3.7)
- [ ] Redis sliding-window rate limits (60 req/min) to prevent bot scraping (§4.1)
- [ ] Photo upload flow for handwritten text answers (camera or file upload; student hand-drawn diagram marking explicitly out of scope for v1)
- [ ] OCR extraction of answer text from the uploaded image (§3.4)
- [ ] `answers` schema: `answer_image_url`, `ocr_text` columns
- [ ] Route OCR output through the existing DSL/AI marking pipeline unchanged

### Phase 5 — Growth & Freshness Features
*Goal: features that increase student engagement and practice freshness.*

- [ ] Custom paper generation (bank-assembled first, free-form AI generation second)
- [ ] Postgres schema: `question_variants`
- [ ] AI-generated question variants derived from existing bank questions, validated against the source mark scheme before entering the pool (§3.6)
- [ ] Variant serving mixed into paper generation and the adaptive practice queue for freshness
- [ ] Deeper analytics on user practice activity, mistake resolution, and retention
- [ ] Iteration on adaptive-queue tuning based on real usage data
