# yt-artist Development Session Summary

*Comprehensive summary of all 22 development sessions.*

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

## Sessions 11-13: BAML Scoring + DB Templates + Hallucination Guardrails (405 tests)

**Goal:** BAML for typed scoring prompts; DB-stored templates for summarization; 3-tier hallucination guardrails.

### New files

| File | Purpose |
|------|---------|
| `baml_src/clients.baml` | Ollama + OpenAI client configs with exponential retry |
| `baml_src/score.baml` | ScoreSummary → ScoreRating, VerifyClaims → ClaimVerification[] |
| `baml_src/generators.baml` | Python/Pydantic code generation config |
| `src/yt_artist/prompts.py` | Thin adapter: 2 scoring functions wrapping baml_client.b, re-exports types |

### New test module: `test_prompts.py` (11 tests)

- BAML adapter function tests with mocked `baml_client.b`
- Anti-hallucination content assertions on summarizer prompts and score.baml

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

- Removed 4 hardcoded prompt constants, replaced with DB-stored templates rendered via `_fill_template()` + `llm.complete()`
- Internal chunk/reduce/refine prompts are module-level constants (`_CHUNK_SYSTEM_PROMPT`, `_REDUCE_SUFFIX`, `_REFINE_SYSTEM_PROMPT`)
- User's custom template controls single-pass + final reduce; chunk/refine use internal prompts

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
- `test_chunked_summarize.py`, `test_summarizer.py`: mocks target `llm_complete` (not prompts module)
- `test_r8_empty_summary.py`, `test_ux_improvements.py`: minor mock path updates

### Documentation

- ADR-0014: BAML scoring and hallucination guardrails
- Architecture diagrams updated with 2 new diagrams (#15 Prompt Architecture, #16 Guardrails)

---

## Session 14: Parallelism for Scoring + Map-Reduce (414 tests)

**Goal:** Eliminate two remaining sequential bottlenecks identified during async audit.

### Changes to `cli.py`

- `_cmd_score()`: replaced sequential `for` loop with `ThreadPoolExecutor` + `_ProgressCounter`
- Uses existing `--concurrency` global arg (clamped to [1, MAX_CONCURRENCY=3])
- Added `--dry-run` support to score command (was the only bulk command missing it)
- Background job integration via `_ProgressCounter(job_id=_bg_job_id, ...)`

### Changes to `summarizer.py`

- `_summarize_map_reduce()`: map phase parallelized with `ThreadPoolExecutor`
- New constant `_MAP_CONCURRENCY` (default 3, env var `YT_ARTIST_MAP_CONCURRENCY`)
- Single-chunk fast path skips pool overhead
- Results reassembled in original chunk order (keyed by index)

### New tests (9 tests)

| Test | Module | Purpose |
|------|--------|---------|
| `test_parallel_produces_correct_output` | test_chunked_summarize | Parallel map path, correct reduce call |
| `test_chunk_ordering_preserved` | test_chunked_summarize | Variable-delay chunks → ordered reassembly |
| `test_chunk_error_propagates` | test_chunked_summarize | One chunk failure → exception propagation |
| `test_concurrency_one_uses_sequential_path` | test_chunked_summarize | `_MAP_CONCURRENCY=1` → no pool |
| `test_single_chunk_skips_pool` | test_chunked_summarize | Single chunk → sequential regardless of setting |
| `test_parallel_scoring_produces_correct_results` | test_scorer | Concurrent scoring scores all summaries |
| `test_score_error_does_not_block_others` | test_scorer | Error isolation: 1 failure, 2 succeed |
| `test_score_dry_run` | test_scorer | `--dry-run` prints count, no DB writes |
| `test_score_single_concurrency_regression` | test_scorer | `concurrency=1` still works |

### Design decisions

- **Custom parallel loop** in `_cmd_score()` (not `_run_bulk()`) because score needs richer per-item output (quality, faithfulness, verification, LOW FAITHFULNESS markers)
- **Nested ThreadPoolExecutors**: map-reduce runs inside `_run_bulk()` workers; Python allows this, total threads bounded at 3 × 3 = 12
- **Ollama note**: local Ollama processes requests sequentially; parallel chunks only help against OpenAI API. Users can set `YT_ARTIST_MAP_CONCURRENCY=1` to disable

---

## Session 15: Architecture Review Items 6–9 (487 tests)

**Goal:** Complete the final 4 items from the 9-item architecture review.

### Item 7: config.py — Environment Variable Centralization

**New module: `config.py` (~140 lines)**

| Dataclass | Env vars covered |
|-----------|-----------------|
| `YouTubeConfig` | inter_video_delay, sleep_requests, sleep_subtitles, cookies_browser, cookies_file, po_token |
| `LLMConfig` | base_url, api_key, model, is_ollama (derived) |
| `AppConfig` | log_level, data_dir_env, db_env, default_prompt, max_transcript_chars, summarize_strategy |
| `ConcurrencyConfig` | max_concurrency, map_concurrency + `split_budget()` method |

Accessor functions: `get_youtube_config()`, `get_llm_config()`, `get_app_config()`, `get_concurrency_config()` — each `@lru_cache(maxsize=1)`.

**Callers updated:** yt_dlp_util.py, summarizer.py, llm.py, cli.py, mcp_server.py.

**New test module:** test_config.py (15 tests)

### Item 6: Concurrency Centralization

`ConcurrencyConfig.split_budget()` replaces standalone `_split_concurrency()` in pipeline.py. `MAX_CONCURRENCY` in yt_dlp_util.py becomes a backward-compat re-export from config. `_MAP_CONCURRENCY` in summarizer.py replaced with `get_concurrency_config().map_concurrency`.

### Item 8: `--json` Output Mode

**Changes to `cli.py`:**
- `--json` global flag (`dest="json_output"`)
- `_json_print(data, args)` helper: returns True if JSON printed

**Commands with JSON support:**

| Command | JSON shape |
|---------|-----------|
| `list-prompts` | `[{"id", "name", "template"}]` |
| `search-transcripts` | `[{"video_id", "artist_id", "transcript_len", "title"}]` |
| `status` | `{"artists", "videos", "transcribed", ...}` |
| `jobs list` | `[{"id", "status", "done", "total", "started_at", "command"}]` |
| `doctor` | `{"checks": [{"name", "status", "message"}], "ok", "warn", "fail"}` |

**New test module:** test_json_output.py (13 tests)

### Item 9: `set-about` Command

**New subcommand:** `set-about --artist-id @X "about text"` — manually set artist description without DuckDuckGo search or LLM calls. Supports `--json` output.

**New tests in test_cli.py:** 3 tests (happy path, unknown artist, JSON output)

---

## Session 16: Transcript Quality, Timestamps, BG Health Check (537 tests)

**Goal:** Three parking lot items — pre-summarize transcript quality scoring (#8), raw VTT storage (#22), BG worker health check (#11).

**Changes:**
- New `transcript_quality.py` module: heuristic transcript scoring (word count, repetition, avg word length, punctuation density, line uniqueness). `--skip-low-quality` flag on summarize.
- `raw_vtt` column on transcripts table. `--include-vtt` on export. VTT preserved alongside plain text.
- Background worker health check: child writes "started" marker, parent verifies within 5s.
- Doctor check [6/6] for transcript quality availability.

**New test modules:** test_transcript_quality, additions to test_exporter, test_background_jobs

---

## Session 17: Export/Backup (570 tests)

**Goal:** Data portability — chunked JSON and flat CSV export.

**Changes:**
- New `exporter.py` module: `export_json()` (chunked per-artist, N videos/file) and `export_csv()` (flat relational tables)
- `--zip` compresses each file. `--include-vtt` includes raw timestamps. `--chunk-size N` configurable.
- Manifest.json with export metadata, file sizes, per-artist stats.
- CLI: `export` subcommand with `--format`, `--artist-id`, `--zip`, `--include-vtt`, `--chunk-size`, `--json` flags.

**New test module:** test_exporter

---

## Session 18: CLI Structural Refactor (594 tests)

**Goal:** Replace module globals and decompose `_cmd_summarize` (parking lot #23).

**Changes:**
- `AppContext` dataclass: holds args, storage, data_dir, quiet, bg_job_id, bg_storage
- Threaded through all 15 `_cmd_*` handlers, replacing `_quiet`/`_bg_job_id`/`_bg_storage` globals
- `_cmd_summarize` decomposed: `_summarize_single()`, `_summarize_bulk_sequential()`, `_summarize_pipeline()` extracted
- `_hint()` and `_run_bulk()` accept explicit params instead of reading globals

**Test modules updated:** test_cli, test_background_jobs, test_pipeline

---

## Session 19: Work Ledger + Summary Provenance (621 tests)

**Goal:** Operation audit trail (#24) and staleness detection (#27).

**Changes:**

### Work Ledger (ledger.py, ~120 lines)
- Append-only `work_ledger` table: operation, video_id, artist_id, status, elapsed_ms, model, prompt_id, strategy, error
- `WorkTimer` context manager for timing. `record_operation()` best-effort write (never raises).
- Domain functions (transcriber, summarizer, scorer) auto-log operations.
- CLI `history` command. Ledger counts in `status`.

### Summary Provenance (hashing.py + storage changes)
- `content_hash()` utility (SHA-256 hex digest)
- `prompt_hash TEXT`, `transcript_hash TEXT` columns on summaries table
- `get_stale_summary_counts()` and `get_stale_video_ids()` for staleness detection
- `--force`/`--stale-only` flags on summarize. `status` shows stale breakdown.

**New test modules:** test_ledger, test_hashing, additions to test_storage

---

## Session 20: Encapsulate DB Connections (637 tests)

**Goal:** Eliminate all external `storage._conn()` calls (parking lot #25).

**Changes:**
- 11 new Storage methods: `create_job`, `update_job_pid`, `get_job`, `update_job_progress`, `finalize_job`, `mark_job_stale`, `list_recent_jobs`, `delete_old_jobs`, `log_rate_request`, `count_rate_requests`, `get_unscored_transcripts`
- `JobRow` TypedDict added
- 21 test `_conn()` calls replaced with `transaction()` context manager
- Fixed `transcriber.py` line 323 direct `os.environ` read → `get_youtube_config().po_token`
- Zero `_conn()` calls remain outside `storage.py`

**Test modules updated:** test_storage, test_background_jobs, test_rate_limit, test_cli

---

## Session 21: Profiling + Parking Lot Triage

**Goal:** Determine if performance language migration (Rust/C) is worthwhile.

**Changes:**
- Profiled all CPU-bound functions. Heaviest: `_key_term_coverage()` at ~15ms, followed by 5-30s LLM calls.
- Closed parking lot #12 as "won't do" — Python is not the bottleneck, I/O is.
- Created 3 new items from profiling: #28 (N+1 export queries), #29 (single-pass transcript scoring), #30 (staleness hash caching).

**No code changes.** Documentation-only session (PARKING_LOT.md updated).

---

## Session 22: FTS5 Search, MCP Fix, Export Optimization (660 tests)

**Goal:** Three parking lot items — FTS5 transcript search (#26), MCP dependency fix (#7), N+1 export fix (#28).

**Changes:**

### FTS5 Full-Text Transcript Search (#26)
- `transcripts_fts` FTS5 virtual table (external content, content_rowid=rowid)
- Sync triggers: `transcripts_ai` (INSERT), `transcripts_ad` (DELETE), `transcripts_au` (UPDATE)
- `_migrate_fts5_transcripts()` with FTS5 availability check, `rebuild` for existing transcripts
- `search_transcripts()` Storage method: BM25-ranked, snippet extraction (`snippet()` with `[`/`]` markers)
- CLI: `--query`/`-q` FTS5 search, `--limit` result cap, dual-mode (list vs search)
- MCP: `search_transcripts` tool with query/list dual mode
- Doctor: FTS5 check [7/7]
- ADR-0015 documenting design decisions

### MCP Dependency Fix (#7)
- PEP 508 environment marker: `mcp>=1.0.0; python_version>='3.10'` in pyproject.toml
- `mcp_server.py` lazy import guard unchanged

### N+1 Export Fix (#28)
- `get_transcripts_for_videos(video_ids)` and `get_summaries_for_videos(video_ids)` batch methods using `_execute_chunked_in()`
- `_build_video_entry()` refactored: accepts pre-fetched transcript/summaries instead of per-video DB queries
- `export_json()`: 2 batch queries per chunk (was 2×chunk_size)
- `export_csv()`: 2 total queries (was ~2000 for 500 videos)

**New test modules:** test_fts_search (26 tests), additions to test_storage (9 batch tests), test_exporter updated

---

## Final Project State

### Source code (~7,500 lines across 21 modules)

| Module | Lines | Purpose |
|--------|-------|---------|
| `cli.py` | ~1,500 | CLI entry point, all commands, argparse, progress counter |
| `storage.py` | ~720 | SQLite ORM, migrations, all CRUD operations |
| `jobs.py` | 424 | Background job management |
| `transcriber.py` | 404 | Video transcription via yt-dlp subtitles |
| `scorer.py` | ~380 | Quality scoring: heuristic + LLM self-check + entity verify + claim verify |
| `summarizer.py` | ~330 | LLM-powered summarization with chunking strategies |
| `fetcher.py` | 254 | Channel URL fetching, artist/video resolution |
| `yt_dlp_util.py` | 256 | yt-dlp configuration, URL validation, auth helpers |
| `pipeline.py` | ~240 | 3-stage producer-consumer pipeline |
| `llm.py` | ~190 | OpenAI/Ollama client with caching, retry, model resolution |
| `mcp_server.py` | 110 | MCP server for IDE integration |
| `rate_limit.py` | 85 | YouTube rate-limit tracking and warnings |
| `paths.py` | ~55 | Centralized path construction for runtime data files |
| `prompts.py` | 48 | BAML adapter: 2 scoring functions (score_summary, verify_claims) |
| `config.py` | ~140 | Typed frozen dataclasses for all env vars, @lru_cache accessors |
| `exporter.py` | ~400 | Export/backup: JSON (chunked) and CSV with manifest |
| `transcript_quality.py` | ~120 | Pre-summarize transcript quality scoring heuristics |
| `ledger.py` | ~120 | Append-only work ledger: WorkTimer + record_operation |
| `hashing.py` | ~30 | SHA-256 content hashing for staleness detection |
| `artist_prompt.py` | 50 | Artist prompt building |
| `__init__.py` | 8 | Package init |
| `init_db.py` | 7 | DB initialization entry point |

### Tests (37 modules, 660 tests)

| Module | Tests | Purpose |
|--------|-------|---------|
| `test_scorer.py` | 57 | Quality scoring: heuristic, LLM, entity, faithfulness, verification, parallel, dry-run |
| `test_background_jobs.py` | 32+ | Background jobs, progress, CLI integration, retry |
| `test_onboarding.py` | 22 | Hints, quickstart, quiet flag, first-run |
| `test_rate_limit.py` | 19 | Rate logging, thresholds, warnings, migration |
| `test_url_validation.py` | 18 | Channel/video URL validation, error detection |
| `test_chunked_summarize.py` | 23 | Chunking, map-reduce, refine, strategies, parallel map |
| `test_status.py` | 18 | Status command, count methods, format_size |
| `test_pipeline.py` | 17 | Pipeline happy path, errors, progress, termination |
| `test_parallel.py` | 16 | Parallel execution, progress counter |
| `test_dry_run.py` | 14 | --dry-run for transcribe/summarize bulk/single |
| `test_ux_improvements.py` | 14 | LLM caching, truncation, default prompt, version |
| `test_cli.py` | ~28+ | CLI commands end-to-end, set-about, set-default-prompt |
| `test_config.py` | 15 | Config dataclasses: defaults, env var overrides, cache clearing |
| `test_json_output.py` | 13 | --json output for 5 CLI commands |
| `test_storage.py` | ~33 | Storage CRUD, migration, constraints, provenance, context managers, chunked IN |
| `test_llm_connectivity.py` | ~19 | LLM retry, connectivity, error handling, get_model_name |
| `test_paths.py` | 13 | Path construction, integration with Storage.urllist_path |
| `test_yt_dlp_util.py` | 18 | Rate-limit config, cookies, delays, PO token, auth |
| `test_prompts.py` | 11 | BAML scoring adapter, anti-hallucination content checks (summarizer + score.baml) |
| `test_fts_search.py` | 26 | FTS5 search: storage, migration, triggers, CLI, JSON |
| `test_exporter.py` | ~25 | Export JSON/CSV: chunking, manifest, compression, batch fetch |
| `test_transcript_quality.py` | ~15 | Transcript quality heuristics, CLI integration |
| `test_ledger.py` | ~12 | Work ledger: WorkTimer, record_operation, history CLI |
| `test_hashing.py` | ~8 | content_hash, staleness detection, stale counts |
| Others | ~30+ | Fetcher, transcriber, summarizer, edge cases |

### Architecture decisions (15 ADRs)

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
| 0015 | FTS5 full-text transcript search |

---

## Parking Lot (Future Sessions)

24 of 30 items resolved. See [PARKING_LOT.md](PARKING_LOT.md) for the full prioritized list.

Remaining items:
1. Single-pass transcript scoring (#29, P3) — pure refactor, ~1-5ms savings
2. Staleness hash caching (#30, P3) — backfill NULL hashes for legacy summaries
3. Licensing and payment model (#13, P3)
4. Usage metrics tracking (#14, P3)
5. Shippable product readiness (#15, P3)
6. Blog post (#16, P3)
