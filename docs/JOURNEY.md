# The yt-artist Development Journey: Building a CLI Tool with Human-AI Collaboration

*A record of iterative, collaborative development between a human developer and Claude across 22 sessions.*

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

## Sessions 11-12: BAML Scoring + DB-Stored Summarization Templates

**Focus:** Typed scoring prompts via BAML and DB-stored summarization templates.

**The problem:**
All LLM prompts were hardcoded as Python string constants scattered across `summarizer.py` and `scorer.py`. Changing a prompt required editing Python source. No version history on prompt wording. No structured inputs/outputs — just raw string interpolation and manual parsing. The DB `prompts` table existed but templates were never actually sent to the LLM.

**What we built:**

### BAML for scoring (typed outputs)
- Adopted [BAML](https://github.com/BoundaryML/baml) for scoring/verification prompts where typed outputs matter.
- `score.baml`: ScoreSummary → typed ScoreRating (completeness, coherence, faithfulness as integers), VerifyClaims → ClaimVerification[] (claim text + verified bool). No manual parsing.
- Thin `prompts.py` adapter: 2 functions wrapping BAML-generated code so `scorer.py` never imports `baml_client` directly.

### DB templates for summarization (user-customizable)
- `summarizer.py`: DB-stored prompt templates are the actual system prompt sent to the LLM via `_fill_template()` + `llm.complete()`. Users customize via `yt-artist add-prompt`.
- Internal chunk/reduce/refine prompts are module-level constants (not user-customizable) — they're mechanical, not creative.
- `scorer.py`: replaced `_LLM_SCORE_PROMPT` + `_parse_llm_rating()` with `prompts.score_summary()` returning typed `ScoreRating`.

**Result:** 382 tests passing. Scoring prompts have typed outputs via BAML. Summarization prompts are user-customizable via DB templates.

**Design principle:** BAML where typed outputs matter (scoring). DB templates where user customization matters (summarization). Adapter pattern isolates the codebase from BAML internals.

---

## Sessions 12-13: Hallucination Guardrails

**Focus:** Prevent and detect hallucinated names, facts, and claims in summaries.

**The trigger:**
The Huberman Lab willpower episode (`cwakOgHIT0E`, 132K chars) produced a summary attributing the talk to "Elijah Wood" — a name that appeared nowhere in the transcript. Root causes: no faithfulness instructions in prompts, the LLM self-check only saw 2% of the transcript (blind `transcript[:3000]`), and the entity score was averaged away into a single quality number.

**What we built:**

### Tier 1: Prompt hardening (0 extra LLM calls)
All prompts now include explicit anti-hallucination instructions: "Only state facts, names, quotes that appear in the transcript. Do not invent or assume any information." This applies to the DB default template, internal chunk/reduce/refine constants in `summarizer.py`, and `score.baml`.

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

## Session 14: Parallelism for Scoring + Map-Reduce

**Focus:** Eliminate the last two sequential bottlenecks identified during an async audit of the entire codebase.

**What we found:** An audit of every synchronous operation revealed exactly two worth parallelizing:
1. `_cmd_score()` — the only bulk command still running a sequential `for` loop. With 100 summaries × 5s each, scoring took ~500s. Transcribe and summarize already used `ThreadPoolExecutor`.
2. Map-reduce chunk summaries — each chunk summarized sequentially. 10 chunks × 10s = 100s, but chunks are independent (embarrassingly parallel).

**What we built:**
- **Parallel scoring**: Custom `ThreadPoolExecutor` loop in `_cmd_score()` with `_ProgressCounter` for background job integration. Added `--dry-run` support (was the only bulk command missing it). Chose not to reuse `_run_bulk()` because score needs richer per-item output (quality, faithfulness, verification, LOW FAITHFULNESS markers).
- **Parallel map-reduce**: `ThreadPoolExecutor` in `_summarize_map_reduce()` map phase. Results keyed by chunk index, reassembled in order after `as_completed`. New `YT_ARTIST_MAP_CONCURRENCY` env var (default 3, set to 1 for local Ollama which processes sequentially anyway).

**What we didn't change:** Everything else. The async audit evaluated 15+ candidates (asyncio migration, synchronous yt-dlp calls, DB operations, pipeline polling) and rejected them all — either the bottleneck is I/O not Python, the refactor cost exceeds the benefit, or rate limiting is the real constraint.

**Result:** 414 tests passing (9 new).

---

## Session 15: Architecture Review Items 6–9 — Config, Concurrency, JSON, set-about

**Focus:** Final 4 items from the architecture review: env var centralization, concurrency policy centralization, machine-readable output, and simplified artist descriptions.

**What we built:**

### Item 7: config.py — Environment Variable Centralization
- New `config.py` module with 4 typed frozen dataclasses (`YouTubeConfig`, `LLMConfig`, `AppConfig`, `ConcurrencyConfig`) and `@lru_cache` accessor functions.
- All 24+ scattered `os.environ.get()` calls across 6 modules now delegate to config.py. No module reads env vars directly.
- Conftest autouse fixture clears LRU caches between tests.

### Item 6: Concurrency Centralization
- `ConcurrencyConfig` with `max_concurrency`, `map_concurrency`, and `split_budget()` method.
- Replaced hardcoded `MAX_CONCURRENCY=3`, `_MAP_CONCURRENCY`, and `_split_concurrency()` across yt_dlp_util.py, summarizer.py, pipeline.py, and cli.py.

### Item 8: `--json` Output Mode
- Global `--json` flag on CLI with `_json_print()` helper (returns True if JSON printed, enabling early return).
- 5 commands support JSON: `list-prompts`, `search-transcripts`, `status`, `jobs list`, `doctor`.
- Doctor collects structured `{"checks": [...], "ok": N, "warn": N, "fail": N}`.

### Item 9: `set-about` Command
- `yt-artist set-about --artist-id @X "description text"` — directly set artist about text without DuckDuckGo search or LLM calls.
- Simpler alternative to `build-artist-prompt`. Supports `--json` output.

**Result:** 487 tests passing (37 new). 4 new test modules: test_config.py, test_json_output.py + additions to test_cli.py.

**Design principle:** Config centralization is invisible to users but eliminates a class of bugs (misspelled env var names, inconsistent defaults, duplicated reads). JSON output enables scripting and MCP integration without changing human-readable defaults.

---

## Session 16: Transcript Quality, Timestamped Transcripts, BG Health Check

**Focus:** Three parking lot items — pre-summarize transcript quality scoring, raw VTT storage, and background worker health checks.

**What happened:**
- **Transcript quality scoring** (item #8): Heuristic scorer in `transcript_quality.py` — word count, repetition ratio, average word length, punctuation density, line uniqueness. Flags low-quality transcripts before wasting LLM calls. Doctor check added.
- **Timestamped transcripts** (item #22): `raw_vtt` column on transcripts table stores original VTT with timestamps. Available via `--include-vtt` on export.
- **BG worker health check** (item #11): Background child writes a "started" marker within 5 seconds, parent verifies. Documents the known limitation of re-execution pattern.

**Result:** 537 tests passing.

---

## Session 17: Export/Backup

**Focus:** Data portability — users with hundreds of transcribed videos need to know their data isn't locked in SQLite.

**What we built:**
- `yt-artist export --format json` — chunked JSON per artist (N videos per file, self-contained with artist metadata + prompts).
- `yt-artist export --format csv` — flat relational tables (artists, videos, transcripts, summaries, prompts).
- `--zip` compresses each file individually. `--include-vtt` includes raw timestamps. `--chunk-size N` configurable.
- Manifest.json with export metadata, file sizes, and per-artist stats.
- Memory-efficient: iterates per-video, never loads all transcripts at once.

**Result:** 570 tests passing.

**Design principle:** Export files are self-contained. Each JSON chunk includes the artist metadata and referenced prompts — you can read a single chunk file and understand everything in it without the manifest.

---

## Session 18: CLI Structural Refactor

**Focus:** `cli.py` was 1,600 lines with 3 module-level globals and a 297-line `_cmd_summarize`.

**What we built:**
- **AppContext dataclass** — replaced `_quiet`, `_bg_job_id`, `_bg_storage` globals + the `(args, storage, data_dir)` triple with a single context object threaded through all 15 `_cmd_*` handlers.
- **Decomposed `_cmd_summarize`** — extracted `_summarize_single()`, `_summarize_bulk_sequential()`, `_summarize_pipeline()`. The handler is now ~130 lines of setup + dispatch.

**Result:** 594 tests passing.

**Key insight:** We deliberately deferred splitting cli.py into a `commands/` package. With one entrypoint and ~1,600 lines, a single file is navigable. The package split is only justified when adding a second entrypoint (API server, TUI) or crossing ~2,500 lines.

---

## Session 19: Work Ledger + Summary Provenance

**Focus:** Audit trail for operations and staleness detection for summaries.

**What we built:**
- **Work ledger** (item #24): Append-only `work_ledger` table in `ledger.py`. `WorkTimer` for timing, `record_operation()` best-effort write. Domain functions (transcribe, summarize, score) auto-log every operation. CLI `history` command shows recent activity.
- **Summary provenance** (item #27): SHA-256 hashes of prompt template and transcript content stored on summaries. `--force`/`--stale-only` flags on summarize. `status` command shows stale count with breakdown (prompt changed / transcript updated / unknown provenance).

**Result:** 621 tests passing.

**Design principle:** The work ledger is best-effort — `record_operation()` never raises exceptions. A logging failure must never break the operation being logged.

---

## Session 20: Encapsulate DB Connections

**Focus:** Eliminate all external `storage._conn()` calls — the last remaining architectural debt.

**What we built:**
- 11 new Storage methods (`create_job`, `update_job_pid`, `get_job`, `update_job_progress`, `finalize_job`, `mark_job_stale`, `list_recent_jobs`, `delete_old_jobs`, `log_rate_request`, `count_rate_requests`, `get_unscored_transcripts`).
- Replaced 21 test `_conn()` calls with `transaction()` context manager.
- Added `JobRow` TypedDict. Zero `_conn()` calls remain outside `storage.py`.

**Result:** 637 tests passing.

---

## Session 21: Profiling + Parking Lot Triage

**Focus:** Profile the codebase to determine if performance language migration (Rust/C) was worthwhile.

**What we found:** Python is not the bottleneck. Every CPU-bound function runs for single-digit milliseconds, then waits seconds-to-minutes for yt-dlp, LLM, or SQLite I/O. The heaviest pure-Python function (`_key_term_coverage()`) takes ~15ms, followed by a 5-30s LLM call.

**Decision:** Closed parking lot item #12 ("Performance language migration") as "won't do." Created three new Python-level items (#28-30) that address actual measured inefficiencies: N+1 export queries, multi-pass transcript scoring, and staleness hash recomputation.

---

## Session 22: FTS5 Search, MCP Fix, Export Optimization

**Focus:** Three high-value parking lot items in a single session.

**What we built:**

### FTS5 full-text transcript search (item #26)
Users with 500+ transcripts couldn't search within transcript text. We added an FTS5 external content table synced via triggers, BM25-ranked results with snippet context, `--query`/`-q` and `--limit` CLI flags, and dual-mode behavior (list without `--query`, search with it). 26 new tests. ADR-0015 documents the design.

### MCP dependency fix (item #7)
`mcp>=1.0.0` requires Python ≥3.10 but the project supports ≥3.9. One-line fix: PEP 508 environment marker `mcp>=1.0.0; python_version>='3.10'` in pyproject.toml. `mcp_server.py` already had a lazy import guard.

### N+1 export query fix (item #28)
`export_csv()` made ~2,000 DB queries for 500 videos. Added `get_transcripts_for_videos()` and `get_summaries_for_videos()` batch methods, refactored `_build_video_entry()` to accept pre-fetched data. Reduced to ~2 total queries.

**Result:** 660 tests passing. Parking lot scorecard: 24 of 30 items resolved.

---

## What We Built: By the Numbers

| Metric | Value |
|--------|-------|
| Source files | 21 Python modules + 3 BAML files (scoring only) |
| Source lines | ~7,500 |
| Test files | 37 test modules |
| Total tests | 660 |
| ADRs | 15 (0001-0015) |
| New modules created | `jobs.py`, `pipeline.py`, `rate_limit.py`, `scorer.py`, `prompts.py`, `paths.py`, `config.py`, `transcript_quality.py`, `ledger.py`, `exporter.py`, `hashing.py` |
| Sessions | 22 |
| Test growth | 81 → 99 → 109 → 138 → 170 → ~225 → ~270 → 308 → 325 → 378 → 405 → 414 → 487 → 537 → 570 → 594 → 621 → 637 → 660 |

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

24 of 30 parking lot items resolved. 2 remaining technical items, 4 product-direction items:

1. **Single-pass transcript scoring** (#29): Collapse 5 separate text passes into 1-2. Currently ~1-5ms/video — correctness fix, not performance-critical.
2. **Staleness hash caching** (#30): Backfill NULL hashes for legacy summaries so staleness checks are pure SQL comparisons.
3. **Licensing and payment** (#13): Freemium model design for potential consumer product.
4. **Usage metrics tracking** (#14): Counters for summaries, transcripts, API calls, processing time.
5. **Shippable product readiness** (#15): PyPI, Homebrew tap, GitHub Actions CI/CD.
6. **Public blog post** (#16): Polish JOURNEY.md for external audience.

---

*This document was written as part of the yt-artist project to record the collaborative development process and the lessons learned from building software through human-AI partnership.*
