# The yt-artist Development Journey: Building a CLI Tool with Human-AI Collaboration

*A record of iterative, collaborative development between a human developer and Claude across 13 sessions.*

---

## The Starting Point

yt-artist began as a Python CLI tool for fetching YouTube channel video URLs, transcribing videos (via yt-dlp subtitles), and generating AI-powered summaries using local Ollama or OpenAI-compatible APIs. The initial codebase had the core data model (SQLite with artists, videos, transcripts, prompts, summaries), basic CLI commands, and an MCP server for IDE integration.

The code worked, but it was rough. We set out to make it production-quality through a series of focused sessions.

---

## Session 1: Foundation Audit and Portability

**Focus:** Code quality, portability, test reliability.

**What happened:**
- Deep audit of the entire codebase: storage, CLI, transcriber, summarizer, fetcher, LLM client.
- Found and fixed portability issues (hardcoded paths, macOS assumptions that broke on other systems).
- Fixed test reliability issues (tests that depended on execution order or global state).
- Improved error messages and edge case handling.

**Result:** 81 tests passing. The codebase went from "it works on my machine" to "it works reliably and tests prove it."

**Lesson learned:** Starting with a thorough audit before adding features pays enormous dividends. Every subsequent session benefited from the solid test foundation.

---

## Session 2: User Frustration Audit and UX

**Focus:** Making the tool pleasant to use.

**What happened:**
The human asked a pointed question: *"What would frustrate a real user?"* This shifted the focus from code quality to user experience.

We identified and fixed 6 frustration points, then did a broader UX/performance audit that led to 9 improvements:
- LLM client caching (don't reconnect on every summarize call)
- DB fast-path for artist/video lookup (skip yt-dlp when data exists)
- Transcript truncation (don't send 100K chars to the LLM)
- Default prompt created on fresh DB (so summarize works immediately)
- Better error messages, version flag, search header formatting

**Result:** 99 tests passing. The tool went from "functional" to "thoughtful."

**Key insight from the human:** *"Think about what would frustrate someone using this for the first time."* This reframing was more valuable than any technical improvement. It taught me to evaluate features from the user's chair, not the developer's.

---

## Session 3: Parallelism

**Focus:** Speed for bulk operations.

**What happened:**
- Audited which operations were I/O-bound and would benefit from parallelism.
- Implemented `ThreadPoolExecutor` for bulk transcribe and summarize.
- Added batch DB queries to skip already-processed videos.
- Added speculative subtitle download (try auto-subs first).
- Thread-safe progress counter with locking.

**Result:** 109 tests passing. Bulk operations ~2x faster with conservative default concurrency.

**Design principle:** Conservative defaults (2 workers), aggressive configurability (env vars for everything). Users who know what they're doing can crank it up; new users are safe by default.

---

## Session 4: Rate-Limit Safety + Guided Onboarding

**Focus:** Two seemingly unrelated concerns that both serve new users.

### Rate-limit safety
- Research into YouTube's rate-limiting behavior.
- Added sleep flags, inter-video delays, max concurrency caps.
- Optimistic subtitle download with fallback.
- Cookie support for restricted content.
- 7 fixes, all configurable via environment variables.

### Guided onboarding
The human's insight: *"A new user runs one command and has no idea what to do next."*

This led to:
- Next-step hints after every command (to stderr, never stdout).
- `--quiet`/`-q` flag for scripting (power users).
- `quickstart` subcommand: a concrete, copy-pasteable walkthrough using @TED.
- First-run detection: empty DB triggers a "try quickstart" suggestion.

**Result:** 138 tests passing. The tool became self-teaching.

**Key insight:** The `--quiet` flag was non-negotiable. Every hint system needs an escape hatch. Without it, the tool would annoy the exact power users who are its most valuable advocates.

---

## Session 5: Background Jobs

**Focus:** Non-blocking long-running operations.

**The user's request:** *"If someone issues a long running bulk command, there should be a suggestion to run it in background with estimated time. Also there should be an option to bring it to foreground and watch how it's going. But if it is pushed to background other tasks the user issues should not get blocked by it."*

**What we built:**
- `--bg` flag: pushes any bulk operation to a detached background process.
- `yt-artist jobs`: list all jobs with progress (done/total).
- `jobs attach <id>`: tail the log file in real time.
- `jobs stop <id>`: send SIGTERM for graceful shutdown.
- `jobs clean`: remove old finished jobs.
- Time estimation: before bulk operations with 5+ videos, show estimated duration and suggest `--bg`.
- Crash safety: stale PID detection, SIGTERM handler, try/except wrapping.

**Technical decision:** OS-level process detachment (`subprocess.Popen(start_new_session=True)`) over task queues (Celery/Redis), threading (can't survive terminal close), or multiprocessing (fork + SQLite = fragile). Zero new dependencies.

**Result:** 170 tests passing. Users can fire-and-forget long operations.

---

## Session 6: URL Validation & Security Docs (Phase 1)

**Focus:** Safe-fail on bad YouTube URLs and security transparency.

**What happened:**
- Added URL format validation before calling yt-dlp — YouTube channel and video URLs are validated with clear error messages for malformed input, private/deleted videos, and age-restricted content.
- 18 validation tests covering @handle, /channel/, /c/, /user/ formats, youtu.be short links, shorts, and embed URLs.
- Added "Security considerations" section to USER_GUIDE.md: unencrypted DB warning, cookie file sensitivity, .gitignore coverage.

**Result:** ~225 tests passing. New users hitting bad URLs get actionable errors instead of cryptic yt-dlp stderr.

---

## Session 7: DRY Refactor, LLM Retry, Job Retry (Phase 2)

**Focus:** Resilience and reducing code duplication.

**What happened:**
- DRY refactoring of yt-dlp subprocess calls — consolidated duplicate patterns across fetcher, transcriber, and yt_dlp_util.
- LLM retry with exponential backoff — transient Ollama/OpenAI failures no longer crash bulk operations.
- `jobs retry <id>` command — re-launch failed background jobs, picking up from where they left off.

**Result:** ~270 tests passing. Bulk operations survive transient failures gracefully.

---

## Session 8: Rate-Limit Monitoring, Status, --dry-run (Phase 3)

**Focus:** Visibility and safety for bulk operations.

**What happened:**
- Rate-limit monitoring: new `request_log` table in SQLite tracks all yt-dlp requests. `rate_limit.py` module provides warnings at 200/hr and 400/hr thresholds. Auto-cleanup of logs older than 24 hours.
- `status` command: single-command overview of artists, videos, transcripts, summaries, prompts, running jobs, and DB size.
- `--dry-run` for bulk operations: shows what would happen (counts, time estimates) without performing any work. 14 tests covering transcribe and summarize dry-run paths.

**Result:** 308 tests passing. Users can see exactly what they have and preview bulk operations before committing.

**Design principle:** Rate-limit monitoring is passive by default — it warns but never blocks. Users in a hurry can acknowledge and continue.

---

## Session 9: Pipeline Parallelism (Phase 4)

**Focus:** Eliminate the transcribe-then-summarize bottleneck.

**What happened:**
The human identified the single biggest performance problem from real-world usage: bulk summarize on @hubermanlab (459 videos) blocked Ollama for 10+ hours while YouTube transcripts trickled in. Transcribe is YouTube I/O-bound; summarize is LLM I/O-bound. These are independent bottlenecks.

We built a producer-consumer pipeline:
- New `pipeline.py` module: DB-polling coordination (not queue-based), concurrency budget splitting, daemon poller thread.
- Transcribe workers feed transcripts into SQLite; summarize workers poll for new transcripts and process them concurrently.
- Time to first summary drops from hours to ~15 seconds.
- Pipeline only activates when bulk summarize discovers missing transcripts. Standalone transcribe and summarize commands unchanged.

**Technical decision:** DB-polling over in-memory queue. Simpler, naturally idempotent, crash-recoverable. The poller checks every 5s with 0.5s wake-up increments for responsive termination.

**Result:** 325 tests passing. 17 pipeline tests covering happy path, error isolation, progress tracking, termination, inter-delay, and CLI integration.

---

## Session 10: Long-Transcript Summarization & Quality Scoring

**Focus:** Full-transcript summarization and automated quality assessment.

**The problem:**
Huberman Lab transcripts are 30K–160K chars. The Mistral 7B context window is ~30K chars. We were silently truncating up to 80% of long transcripts — users had no idea what was lost.

**What we built:**

### Chunking + strategies (summarizer.py, ~265 lines added)
- `_chunk_text()`: sentence-boundary splitting with configurable overlap, clamped to prevent infinite loops.
- `_summarize_map_reduce()`: chunk → summarize each → combine → recursive reduce if still too long.
- `_summarize_refine()`: iterative rolling summary — best coherence for narrative content.
- `auto` strategy (new default): single-pass if fits, map-reduce if too long.
- `--strategy` CLI flag: `auto`, `truncate`, `map-reduce`, `refine`.

### Quality scoring (scorer.py, new ~220 lines)
Decoupled from summarization — scoring is a separate pipeline stage.
- Heuristic scoring: length ratio, repetition detection, key-term coverage, structural analysis. Instant, zero LLM cost.
- LLM self-check: tiny prompt asking model to rate completeness/coherence/faithfulness 1–5.
- Combined: `quality_score = 0.4 * heuristic + 0.6 * llm`. Falls back to heuristic-only on LLM failure.

### 3-stage pipeline (pipeline.py, ~60 lines added)
- transcribe → summarize → score running concurrently via DB-polling.
- Scoring auto-skips when estimated runtime >3h (override with `--score`).

### CLI integration
- `yt-artist score --artist-id @X` — standalone scoring for already-summarized videos.
- `--score`/`--no-score` flags on summarize.
- `status` command shows scoring stats (N scored, avg quality).

**Result:** 378 tests passing (53 new). Full transcripts summarized instead of truncated. Quality visible at a glance.

**Key technical fix:** The chunking overlap logic had an infinite loop when `overlap >= chunk_size`. Clamping overlap to `min(overlap, chunk_size // 2)` and ensuring forward progress fixed it.

---

## Sessions 11-12: BAML Prompt Management

**Focus:** Versioned, typed prompt functions and eliminating hardcoded prompt strings.

**The problem:**
All LLM prompts were hardcoded as Python string constants scattered across `summarizer.py` and `scorer.py`. Changing a prompt required editing Python source. No version history on prompt wording. No structured inputs/outputs — just raw string interpolation and manual parsing.

**What we built:**

### BAML integration
- Adopted [BAML](https://github.com/BoundaryML/baml) (Boundary AI Markup Language) for typed prompt functions.
- 4 `.baml` files in `baml_src/`: prompt definitions with explicit input/output types, Ollama + OpenAI client configs, code generation settings.
- `baml-cli generate` produces `baml_client/` (auto-generated, gitignored). Prompt changes are git diffs of `.baml` files — no Python editing needed.
- Thin `prompts.py` adapter: 6 functions wrapping BAML-generated code so the rest of the codebase never imports `baml_client` directly.

### Prompt refactoring
- `summarizer.py`: removed 4 hardcoded prompt constants, replaced with `prompts.summarize_single_pass()`, `prompts.summarize_chunk()`, `prompts.reduce_chunk_summaries()`, `prompts.refine_summary()`.
- `scorer.py`: replaced `_LLM_SCORE_PROMPT` + `_parse_llm_rating()` with `prompts.score_summary()` returning typed `ScoreRating` (completeness, coherence, faithfulness as integers). No manual parsing.

**Result:** 382 tests passing. Prompts are now versioned files with git history, typed inputs/outputs, and zero manual string parsing.

**Design principle:** Adapter pattern isolates the codebase from BAML internals. If BAML is ever replaced, only `prompts.py` changes.

---

## Sessions 12-13: Hallucination Guardrails

**Focus:** Prevent and detect hallucinated names, facts, and claims in summaries.

**The trigger:**
The Huberman Lab willpower episode (`cwakOgHIT0E`, 132K chars) produced a summary attributing the talk to "Elijah Wood" — a name that appeared nowhere in the transcript. Root causes: no faithfulness instructions in prompts, the LLM self-check only saw 2% of the transcript (blind `transcript[:3000]`), and the entity score was averaged away into a single quality number.

**What we built:**

### Tier 1: Prompt hardening (0 extra LLM calls)
Every `.baml` prompt now includes explicit anti-hallucination instructions: "Only state facts, names, quotes that appear in the transcript. Do not invent or assume any information."

### Tier 2: Scoring guardrails (0 extra LLM calls)
- **Named entity verification** (`_named_entity_score()`): Regex-extracts proper nouns from summaries (multi-word names like "Elijah Wood", single mid-sentence capitalized words). Filters stopwords (months, days, sentence-start words). Checks each entity against the transcript. Score = verified/total, weight = 0.20 of heuristic score. The Elijah Wood hallucination now scores ~0.0 on this metric.
- **Stratified transcript sampling** (`_sample_transcript()`): Replaced blind `transcript[:3000]` with start/middle/end sampling (~1000 chars each). The LLM self-check now sees representative content from the entire transcript.
- **Faithfulness tracking**: The LLM `ScoreRating.faithfulness` is now extracted as a separate `faithfulness_score` column instead of being averaged into `llm_score`. Summaries with faithfulness ≤ 0.4 trigger a warning and CLI marker `[!LOW FAITHFULNESS]`.

### Tier 3: Claim verification (1 extra LLM call, opt-in)
- `--verify` flag on `score` command. The `VerifyClaims` BAML function extracts 5 factual claims from the summary and cross-references each against the transcript. Returns typed `ClaimVerification[]` with claim text and verified boolean.
- `verification_score` stored in a new DB column.

**Result:** 405 tests passing (23 new). ADR-0014 documenting the full architecture.

**Key insight from the human:** The 3-tier approach was deliberate: Tier 1 and 2 run on every summary at zero LLM cost. Tier 3 is opt-in because it adds 1 LLM call per summary — users pay only for the verification they need.

---

## What We Built: By the Numbers

| Metric | Value |
|--------|-------|
| Source files | 16 Python modules + 4 BAML prompt files |
| Source lines | ~6,000 |
| Test files | 28 test modules |
| Total tests | 405 |
| ADRs | 14 (0001-0014) |
| New modules created | `jobs.py`, `pipeline.py`, `rate_limit.py`, `scorer.py`, `prompts.py` |
| Sessions | 13 |
| Test growth | 81 → 99 → 109 → 138 → 170 → ~225 → ~270 → 308 → 325 → 378 → 405 |

---

## What the Human Taught the AI

1. **"What would frustrate a user?"** — The single most impactful reframe. Technical correctness is necessary but not sufficient. User experience is the product.

2. **Conservative defaults, aggressive configurability.** — Don't make users read docs to be safe. Don't make power users read code to go fast.

3. **Every hint needs an off switch.** — `--quiet` was insisted upon before a single hint was written. The human understood that helpful-by-default becomes annoying-always without an escape hatch.

4. **Ship incrementally, test always.** — Every session ended with all tests green. No "we'll fix the tests later." The human pushed for this discipline and it prevented regression cascades.

5. **Think in workflows, not features.** — The `quickstart` command and next-step hints weren't features; they were a workflow made visible. The human consistently thought about the end-to-end user journey.

---

## What the AI Learned About Working Together

1. **Plan before coding.** — The plan-then-implement pattern (used formally from Session 5 onward) produced better architecture and fewer rewrites. When the human approved the plan, implementation was faster because scope was locked.

2. **Show your reasoning.** — The human valued seeing alternative approaches and trade-offs (e.g., "Celery vs subprocess vs threading" in the background jobs plan). Transparency builds trust.

3. **Test-first isn't just quality; it's communication.** — Test names like `test_quiet_suppresses_hints` communicate intent better than comments. They became a shared language for "what this feature does."

4. **Match the codebase's style.** — Every new file followed existing patterns (same import style, same test helper patterns, same docstring format). Consistency signals care.

5. **Respect the parking lot.** — When the human said "Everything below goes to a parking lot for now," that was a scope boundary. Respecting it meant finishing the current work properly instead of spreading thin.

---

## How to Improve Future Sessions

### For the human
- **Start with the user story.** Your best prompts were user-focused: "What would frustrate someone?" and "There should be a way to bring it to foreground." These gave clearer direction than technical specifications.
- **Keep asking "what else?"** after each feature. Your iterative deepening (audit → fix → audit again) found issues that a single pass would miss.
- **Consider writing acceptance criteria up front.** For the background jobs feature, the natural-language description was excellent; for future complex features, a short list of "must pass" scenarios would speed up planning.

### For the AI
- **Propose the plan proactively.** Don't wait to be asked. When the task is non-trivial, immediately suggest entering plan mode.
- **Show test counts after every change.** The human cared deeply about test counts. Make "all N tests pass" the closing line of every implementation block.
- **Flag trade-offs explicitly.** When making a decision (e.g., "2 workers default"), state the trade-off ("safety vs speed") and let the human override.

### For both
- **Always ask: what should we always ask?** This meta-question from the human was itself the most useful question. Keep asking it.

---

## The Road Ahead (Parking Lot)

Items explicitly deferred for future sessions:

1. **Performance optimization:** Analyze which modules could benefit from Rust/Go/C++ for hot paths (yt-dlp subprocess management, large transcript processing).
2. **Licensing and payment:** Design a freemium model (e.g., free for 10K summaries, paid tiers for 50K+). What usage metrics do users need to see?
3. **Usage metrics tracking:** What counters matter? Summaries generated, transcripts processed, API calls made, processing time.
4. **Shippable product:** Package for distribution (PyPI, Homebrew tap). Onboarding flow for non-technical users. Documentation polish.

---

*This document was written as part of the yt-artist project to record the collaborative development process and the lessons learned from building software through human-AI partnership.*
