# yt-artist Development Session Summary

*Comprehensive summary of all 13 development sessions.*

---

## Session 1: Foundation Audit and Portability (81 tests)

**Goal:** Audit the codebase for quality, portability, and test reliability.

**Changes:**
- Fixed hardcoded paths and macOS-specific assumptions throughout the codebase
- Improved error handling in storage, CLI, transcriber, and fetcher modules
- Fixed test isolation issues (global state leaks between tests)
- Added conftest.py for shared test fixtures
- Strengthened foreign key enforcement and transaction handling

**Test modules:** test_storage, test_cli, test_fetcher, test_transcriber, test_summarizer, test_r1_fk_enforcement, test_r4_transaction, test_r5_channel_url, test_r8_empty_summary, test_r10_llm_config, test_r13_template, test_vtt_dedup, test_llm_connectivity

---

## Session 2: User Frustration Audit and UX (99 tests)

**Goal:** Identify and fix what would frustrate real users.

**Changes:**
- LLM client caching: reuse client across summarize calls (don't reconnect every time)
- DB fast-path: skip yt-dlp lookup when artist/video already in database
- Transcript truncation: cap text sent to LLM to prevent context window overflow
- Default prompt: auto-create a "default" prompt on fresh databases
- Version flag: `yt-artist --version`
- Search header: improved formatting for `search-transcripts` output
- Better error messages throughout

**New test modules:** test_ux_improvements, test_ensure_artist_video

---

## Session 3: Parallel Execution (109 tests)

**Goal:** Speed up bulk operations through parallelism.

**Changes:**
- `concurrent.futures.ThreadPoolExecutor` for bulk transcribe and summarize
- Thread-safe `_ProgressCounter` with `threading.Lock`
- Batch DB queries: fetch all existing transcripts/summaries in one query before bulk operations
- Speculative subtitle download: try auto-subs first, fall back to manual
- Configurable concurrency via `YT_ARTIST_MAX_CONCURRENCY` (default: 2)

**New test module:** test_parallel (16 tests)

---

## Session 4: Rate-Limit Safety + Guided Onboarding (138 tests)

### Rate-Limit Safety

**Changes to `yt_dlp_util.py`:**
- `--sleep-requests 1.5` and `--sleep-subtitles 2` flags on all yt-dlp calls
- `get_max_concurrency()`: returns configured max workers, conservative default
- `get_inter_video_delay()`: configurable delay between videos (default 2.0s)
- Cookie support: `YT_ARTIST_COOKIES_BROWSER` and `YT_ARTIST_COOKIES_FILE`

**Changes to `transcriber.py`:**
- Optimistic subtitle download: auto-subs first, manual fallback

**New tests:** 8 tests in test_yt_dlp_util

### Guided Onboarding

**Changes to `cli.py`:**
- `--quiet`/`-q` global flag: suppresses all hints and tips
- `_hint()` helper: writes contextual next-step hints to stderr
- Per-command hints: fetch-channel → transcribe, transcribe → summarize, etc.
- `quickstart` subcommand: 3-step walkthrough using @TED as example
- First-run detection: empty DB suggests `quickstart`
- All hints use real data (actual artist IDs, video IDs, counts)

**New test module:** test_onboarding (22 tests)

---

## Session 5: Background Jobs (170 tests)

### Schema

Added to `schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    pid INTEGER NOT NULL,
    log_file TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
```

Added `_migrate_jobs_table()` to `storage.py`.

### New module: `jobs.py` (381 lines)

| Function | Purpose |
|----------|---------|
| `_generate_job_id()` | 12 hex chars from uuid4 |
| `_is_pid_alive(pid)` | `os.kill(pid, 0)` existence check |
| `estimate_time(n, op, concurrency)` | Wall-clock estimate in seconds |
| `format_estimate(seconds)` | "45s", "3m", "1.5h" |
| `maybe_suggest_background(...)` | Hint to stderr when >=5 videos |
| `launch_background(argv, storage, data_dir)` | Re-exec as detached child |
| `list_jobs(storage)` | Query + stale PID detection |
| `attach_job(storage, job_id)` | Tail log file |
| `stop_job(storage, job_id)` | SIGTERM + status update |
| `cleanup_old_jobs(storage, max_age_days)` | Remove old finished jobs |
| `get_job(storage, job_id)` | Exact + prefix match |
| `update_job_progress(...)` | Update done/errors/total |
| `finalize_job(...)` | Set completed/failed/stopped |

### CLI changes

**New flags:**
- `--bg` / `--background`: run bulk operation in background
- `--_bg-worker JOB_ID`: hidden flag for child process

**New subcommand:** `jobs` with subparsers: (none) = list, `attach`, `stop`, `clean`

**`_ProgressCounter` extension:**
- Optional `job_id` and `job_storage` kwargs
- When set, `tick()` writes to SQLite on each video
- `finalize(status)` sets terminal job status
- No change when `job_id` is None (foreground mode)

**Background dispatch in `main()`:**
- Parent: strips `--bg`, adds `--_bg-worker <id>`, launches child, prints job info, exits
- Child: sets `_bg_job_id` global, registers SIGTERM handler, wraps `args.func()` in crash-safe try/except

### New test module: test_background_jobs (32 tests)

- Time estimation (6 tests)
- Job ID generation (2 tests)
- PID alive check (3 tests)
- Background suggestion (3 tests)
- Job DB CRUD (6 tests)
- ProgressCounter DB integration (3 tests)
- CLI --bg launch (2 tests)
- Jobs subcommand (5 tests)
- Schema migration (2 tests)

---

## Session 6: URL Validation & Security Docs — Phase 1 (~225 tests)

**Goal:** Safe-fail on bad YouTube URLs; security transparency.

**Changes to `yt_dlp_util.py` and `fetcher.py`:**
- YouTube URL validation before yt-dlp calls
- Detection of private/deleted videos from yt-dlp exit codes
- Age-restricted video detection with cookie suggestions

**Changes to `USER_GUIDE.md`:**
- Security considerations section: unencrypted DB, cookie sensitivity

**New test module:** test_url_validation (18 tests)
- TestChannelUrlHappy (9), TestChannelUrlErrors (6)
- TestVideoUrlHappy (7), TestVideoUrlErrors (7)

---

## Session 7: DRY Refactor, LLM Retry, Job Retry — Phase 2 (~270 tests)

**Goal:** Resilience and code deduplication.

**Changes to `llm.py`:**
- Retry with exponential backoff for transient LLM failures

**Changes to `jobs.py` and `cli.py`:**
- `jobs retry <id>` command — re-launch failed jobs

**Changes to `transcriber.py`, `fetcher.py`, `yt_dlp_util.py`:**
- DRY refactoring of yt-dlp subprocess calls

**Updated tests:** test_llm_connectivity, test_background_jobs, test_cli, test_transcriber

---

## Session 8: Rate-Limit Monitoring, Status, --dry-run — Phase 3 (308 tests)

**Goal:** Visibility and safety for bulk operations.

### Schema

Added to `schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    request_type TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_request_log_timestamp ON request_log(timestamp);
```

### New module: `rate_limit.py` (85 lines)

| Function | Purpose |
|----------|---------|
| `log_request(storage, request_type)` | Log yt-dlp request + auto-cleanup |
| `count_requests(storage, hours)` | Count requests in time window |
| `get_rate_status(storage)` | Dict with count_1h, count_24h, warning |
| `check_rate_warning(storage, quiet)` | Print rate warning to stderr |

### CLI changes
- `status` subcommand: artists, videos, transcripts, summaries, prompts, jobs, DB size
- `--dry-run` flag: preview bulk operations without performing work
- Rate warnings before bulk transcribe operations

### New test modules
- test_rate_limit (19 tests): logging, thresholds, warnings, migration
- test_status (18 tests): count methods, format_size, CLI output
- test_dry_run (14 tests): transcribe/summarize bulk/single dry-run

---

## Session 9: Pipeline Parallelism — Phase 4 (325 tests)

**Goal:** Concurrent transcribe + summarize for bulk operations.

### New module: `pipeline.py` (195 lines)

| Function | Purpose |
|----------|---------|
| `PipelineResult` | Dataclass: transcribed, errors, summarized, elapsed |
| `_split_concurrency(total)` | Split workers: c=1→(1,1), c=3→(2,1) |
| `run_pipeline(...)` | Producer-consumer with DB-polling coordination |

### CLI changes

- `_cmd_summarize` bulk path restructured:
  - `if missing:` → pipeline mode (transcribe + summarize concurrent)
  - `else:` → existing sequential `_run_bulk` (unchanged)
- Worker closures, poll_fn, progress counters passed to pipeline
- Pipeline output: "Pipeline: transcribed X, summarized Y new, Z already done (Ns)"

### New test module: test_pipeline (17 tests)

- TestSplitConcurrency (3): budget splitting
- TestPipelineHappyPath (3): full pipeline, mixed state, summarize-only
- TestPipelineErrors (3): error isolation both directions
- TestPipelineProgress (2): tick counts, labels
- TestPipelineTermination (2): completion, empty producer
- TestPipelineDelay (1): inter-video delay enforcement
- TestPipelineCLIIntegration (3): pipeline activation, sequential fallback, background jobs

---

## Session 10: Long-Transcript Summarization & Quality Scoring (378 tests)

**Goal:** Full-transcript summarization and automated quality assessment.

### New module: `scorer.py` (~220 lines)

| Function | Purpose |
|----------|---------|
| `_length_ratio_score(summary_len, transcript_len)` | Score summary/transcript length ratio |
| `_repetition_score(summary)` | Detect sentence-level repetition (model looping) |
| `_key_term_coverage(summary, transcript)` | Top-N transcript term coverage in summary |
| `_structure_score(summary)` | Multi-sentence, bullets, section headers |
| `heuristic_score(summary, transcript)` | Weighted composite: 0.3 length + 0.3 coverage + 0.2 repetition + 0.2 structure |
| `_parse_llm_rating(text)` | Parse "4 3 5" LLM output into 3 integers |
| `llm_score(summary, transcript)` | Tiny LLM call: rate completeness/coherence/faithfulness 1-5 |
| `score_summary(summary, transcript)` | Full scoring: heuristic + optional LLM |
| `score_video_summary(video_id, prompt_id, storage)` | DB-integrated: load, score, save |

### Changes to `summarizer.py` (~265 lines added)

| Function | Purpose |
|----------|---------|
| `_chunk_text(text, chunk_size, overlap)` | Sentence-boundary splitting with clamped overlap |
| `_summarize_map_reduce(system_prompt, raw_text, max_chars)` | Chunk → map → reduce (recursive) |
| `_summarize_refine(system_prompt, raw_text, max_chars)` | Iterative rolling summary |
| `_get_strategy()` | Read strategy from env var |

`summarize()` updated to dispatch by strategy: auto, truncate, map-reduce, refine.

### Changes to `pipeline.py` (~60 lines added)

- `PipelineResult`: added `scored`, `score_errors` fields
- `run_pipeline()`: added `score_fn`, `score_poll_fn`, `score_progress` params
- Stage 3: single-worker scoring after summarize pool completes

### Schema changes

```sql
ALTER TABLE summaries ADD COLUMN quality_score REAL;
ALTER TABLE summaries ADD COLUMN heuristic_score REAL;
ALTER TABLE summaries ADD COLUMN llm_score REAL;
```

### Storage methods added

| Method | Purpose |
|--------|---------|
| `update_summary_scores(video_id, prompt_id, ...)` | Write score columns |
| `get_unscored_summaries(prompt_id, video_ids?)` | Query summaries without quality_score |
| `count_scored_summaries()` | Count summaries with quality_score |
| `avg_quality_score()` | Average quality_score across all scored |

### CLI changes

**New flags on `summarize`:**
- `--strategy {auto,truncate,map-reduce,refine}`: summarization strategy
- `--score` / `--no-score`: force scoring on/off (default: auto based on time estimate)

**New subcommand:** `score`
- `--artist-id @X`: score all summaries for an artist
- `--prompt ID`: prompt to score (default: "default")
- `--skip-llm`: heuristic-only scoring (faster)

**Updated:** `status` command shows scoring stats (N scored, avg quality).

### New test modules

- `test_chunked_summarize.py` (18 tests): chunking, fill_template, strategy, single/map-reduce/refine
- `test_scorer.py` (35 tests): heuristic components, LLM scoring, parse_llm_rating, DB integration

### Bug fixes

- **Infinite loop in `_chunk_text`**: overlap >= chunk_size caused no forward progress. Fixed by clamping `overlap = min(overlap, chunk_size // 2)`.
- **Existing test for truncation**: `test_long_transcript_is_truncated` updated to use `strategy="truncate"` explicitly (default changed from truncate to auto).

---

## Sessions 11-13: BAML Prompt Management + Hallucination Guardrails (405 tests)

**Goal:** Versioned typed prompt functions via BAML; 3-tier hallucination guardrails.

### New files

| File | Purpose |
|------|---------|
| `baml_src/clients.baml` | Ollama + OpenAI client configs with exponential retry |
| `baml_src/summarize.baml` | 4 typed prompt functions: SummarizeSinglePass, SummarizeChunk, ReduceChunkSummaries, RefineSummary |
| `baml_src/score.baml` | ScoreSummary → ScoreRating, VerifyClaims → ClaimVerification[] |
| `baml_src/generators.baml` | Python/Pydantic code generation config |
| `src/yt_artist/prompts.py` | Thin adapter: 6 functions wrapping baml_client.b, re-exports types |

### New test module: `test_prompts.py` (11 tests)

- BAML adapter function tests with mocked `baml_client.b`
- Anti-hallucination content assertions on `.baml` source files

### Changes to `scorer.py` (~380 lines total, up from ~220)

| Function | Purpose |
|----------|---------|
| `_named_entity_score(summary, transcript)` | Regex-extract proper nouns, verify against transcript |
| `_sample_transcript(transcript, max_excerpt)` | Stratified sampling: start + middle + end segments |
| `verify_claims(summary, transcript, model)` | BAML VerifyClaims → ClaimVerification[], verification_score |

**Other changes:**
- `heuristic_score()`: rebalanced weights — 0.25 length, 0.15 repetition, 0.25 coverage, 0.15 structure, 0.20 entity
- `llm_score()`: return type changed from `Optional[float]` → `Optional[Dict[str, float]]` (keys: `llm_score`, `faithfulness`)
- `score_summary()`: added `verify: bool = False` param, faithfulness warning when ≤ 0.4
- `score_video_summary()`: forwards `verify` param, passes faithfulness and verification scores to storage

### Changes to `summarizer.py`

- Removed 4 hardcoded prompt constants (`_MAP_PROMPT`, `_REDUCE_PROMPT_PREFIX`, `_REFINE_PROMPT`, inline system prompt)
- All LLM calls now go through `prompts.*` functions

### Schema changes

```sql
ALTER TABLE summaries ADD COLUMN faithfulness_score REAL;
ALTER TABLE summaries ADD COLUMN verification_score REAL;
```

### Storage methods updated

| Method | Changes |
|--------|---------|
| `update_summary_scores()` | Added `faithfulness_score`, `verification_score` params |
| `_migrate_faithfulness_score_column()` | NEW: migration for faithfulness column |
| `_migrate_verification_score_column()` | NEW: migration for verification column |

### CLI changes

- `score` subparser: `--verify` flag (1 extra LLM call per summary)
- `_cmd_score`: shows `[!LOW FAITHFULNESS]` marker when faithfulness ≤ 0.4, `verified=80%` when verification ran

### Test updates

- `test_scorer.py`: 53 tests (up from 35) — entity score, sampling, faithfulness, verification, DB integration
- `test_chunked_summarize.py`, `test_summarizer.py`: updated mocks for prompts module
- `test_r8_empty_summary.py`, `test_ux_improvements.py`: minor mock path updates

### Documentation

- ADR-0014: BAML prompt management and hallucination guardrails
- Architecture diagrams updated with 2 new diagrams (#15 BAML, #16 Guardrails)

---

## Final Project State

### Source code (~6,000 lines across 16 modules)

| Module | Lines | Purpose |
|--------|-------|---------|
| `cli.py` | ~1,500 | CLI entry point, all commands, argparse, progress counter |
| `storage.py` | ~700 | SQLite ORM, migrations, all CRUD operations |
| `jobs.py` | 424 | Background job management |
| `transcriber.py` | 404 | Video transcription via yt-dlp subtitles |
| `scorer.py` | ~380 | Quality scoring: heuristic + LLM self-check + entity verify + claim verify |
| `summarizer.py` | ~330 | LLM-powered summarization with chunking strategies |
| `fetcher.py` | 254 | Channel URL fetching, artist/video resolution |
| `yt_dlp_util.py` | 256 | yt-dlp configuration, URL validation, auth helpers |
| `pipeline.py` | ~240 | 3-stage producer-consumer pipeline |
| `llm.py` | 182 | OpenAI/Ollama client with caching and retry |
| `mcp_server.py` | 110 | MCP server for IDE integration |
| `rate_limit.py` | 85 | YouTube rate-limit tracking and warnings |
| `prompts.py` | 75 | BAML adapter: 6 typed prompt functions |
| `artist_prompt.py` | 50 | Artist prompt building |
| `__init__.py` | 8 | Package init |
| `init_db.py` | 7 | DB initialization entry point |

### Tests (28 modules, 405 tests)

| Module | Tests | Purpose |
|--------|-------|---------|
| `test_scorer.py` | 53 | Quality scoring: heuristic, LLM, entity, faithfulness, verification, DB |
| `test_background_jobs.py` | 32+ | Background jobs, progress, CLI integration, retry |
| `test_onboarding.py` | 22 | Hints, quickstart, quiet flag, first-run |
| `test_rate_limit.py` | 19 | Rate logging, thresholds, warnings, migration |
| `test_url_validation.py` | 18 | Channel/video URL validation, error detection |
| `test_chunked_summarize.py` | 18 | Chunking, map-reduce, refine, strategies |
| `test_status.py` | 18 | Status command, count methods, format_size |
| `test_pipeline.py` | 17 | Pipeline happy path, errors, progress, termination |
| `test_parallel.py` | 16 | Parallel execution, progress counter |
| `test_dry_run.py` | 14 | --dry-run for transcribe/summarize bulk/single |
| `test_ux_improvements.py` | 14 | LLM caching, truncation, default prompt, version |
| `test_cli.py` | ~25+ | CLI commands end-to-end |
| `test_storage.py` | ~20 | Storage CRUD, migration, constraints |
| `test_llm_connectivity.py` | ~15+ | LLM retry, connectivity, error handling |
| `test_yt_dlp_util.py` | 18 | Rate-limit config, cookies, delays, PO token, auth |
| `test_prompts.py` | 11 | BAML adapter functions, anti-hallucination content checks |
| Others | ~30+ | Fetcher, transcriber, summarizer, edge cases |

### Architecture decisions (14 ADRs)

| ADR | Title |
|-----|-------|
| 0001 | Use yt-dlp for channel listing and transcripts |
| 0002 | Use SQLite for all structured data |
| 0003 | OpenAI-compatible client for summaries |
| 0004 | CLI first, MCP optional |
| 0005 | One markdown urllist per channel |
| 0006 | Prompt template with artist/video/intent/audience |
| 0007 | CLI bulk ops, per-artist default prompt, auto-dependencies |
| 0008 | Background jobs for long-running bulk operations |
| 0009 | Guided onboarding (hints, quickstart, --quiet) |
| 0010 | YouTube rate-limit safety |
| 0011 | Parallel execution with ThreadPoolExecutor |
| 0012 | Pipeline parallelism for bulk transcribe + summarize |
| 0013 | Long-transcript summarization strategies and quality scoring |
| 0014 | BAML prompt management and hallucination guardrails |

---

## Parking Lot (Future Sessions)

These items were explicitly deferred by the user. See [PARKING_LOT.md](PARKING_LOT.md) for the full prioritized list.

Key items:
1. Export/backup (P1)
2. MCP server dependency fix (P1)
3. Transcript quality scoring — pre-summarize (P2, distinct from summary scoring done in Session 10)
4. Performance language migration (P3)
5. Licensing and payment model (P3)
6. Usage metrics tracking (P3)
7. Shippable product readiness (P3)
8. Blog post (P3)
