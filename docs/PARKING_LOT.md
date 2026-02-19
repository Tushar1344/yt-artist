# Parking Lot

Future work items with priority flags. Items marked with a suggestion flag indicate
they were identified during architecture review rather than explicitly requested.

**Priority key:**
- **P0** — Do before shipping (blocks usability or trust)
- **P1** — Do soon (significant user value)
- **P2** — Nice to have (quality of life)
- **P3** — Future vision (product direction)

---

## P0: Ship Blockers

### ~~1. Input validation and safe-fail on bad YouTube URLs~~ `[suggestion]` ✅ Done (Phase 1)
**Why:** Right now, malformed URLs, private/deleted videos, and channels with zero public videos produce cryptic yt-dlp stderr errors. New users will hit this in the first 5 minutes.

**Scope:**
- Validate URL format before calling yt-dlp (is it even a YouTube URL?)
- Detect private/deleted videos from yt-dlp exit codes → clear error message
- Detect empty channels (zero videos) → suggest checking the URL
- Detect age-restricted videos without cookies → suggest `YT_ARTIST_COOKIES_BROWSER`

**Effort:** Small. Mostly wrapping existing yt-dlp calls with better error handling.

---

### ~~2. `--dry-run` for bulk operations~~ `[suggestion]` ✅ Done (Phase 3)
**Why:** Users are nervous about running `transcribe --artist-id @channel` on 500 videos. They want to see what will happen before committing.

**Scope:**
- `yt-artist --dry-run transcribe --artist-id @X` → "Would transcribe 47 videos (23 already done, 24 remaining). Estimated: ~4m"
- `yt-artist --dry-run summarize --artist-id @X` → "Would summarize 31 videos (16 already done, 15 remaining). Estimated: ~4m"
- No actual work performed. Exit code 0.

**Effort:** Small. The batch-query logic already exists; this just skips the execution.

---

### ~~3. Security: warn about unencrypted DB in docs~~ `[suggestion]` ✅ Done (Phase 1)
**Why:** The SQLite file contains all transcripts and summaries in plaintext. If users are summarizing sensitive content (corporate training, private channels), this is a data exposure risk. Users should know.

**Scope:**
- Add a "Security considerations" section to USER_GUIDE.md
- Mention: DB is unencrypted, cookie files contain session tokens, `.gitignore` already excludes both
- Do NOT implement encryption — just inform users

**Effort:** Tiny. Documentation only.

---

## P1: High-Value Features

### ~~4. `status` command~~ `[suggestion]` ✅ Done (Phase 3)
**Why:** There's no single command that shows the state of your data. Users piece it together from `list-prompts`, `search-transcripts`, and `jobs`. This is the `git status` of yt-artist.

**Scope:**
```
$ yt-artist status
Artists:      3 (@TED, @hubermanlab, @lexfridman)
Videos:       847 (312 transcribed, 198 summarized)
Prompts:      2 (default: "short")
Running jobs: 1 (transcribe @lexfridman: 45/502)
DB size:      12.4 MB
```

**Effort:** Small-medium. Query each table, format output.

---

### ~~5. Export/backup~~ `[suggestion]` ✅ Done (Session 17)
**Why:** Users who invest hours transcribing 500 videos want to know their data isn't locked in a SQLite file they can't read. This is a trust issue that blocks adoption.

**Scope:**
- `yt-artist export --artist-id @X --format json` → chunked JSON (per-artist, 50 videos/file, self-contained)
- `yt-artist export --artist-id @X --format csv` → flat tables (artists, videos, transcripts, summaries, prompts)
- `--zip` flag compresses each chunk/CSV into individual `.zip` files (stdlib zipfile, zero deps)
- `--include-vtt` includes raw VTT timestamps; `--chunk-size N` configurable
- Manifest.json with export metadata, file sizes, and artist stats
- Memory-efficient: iterates per-video, never loads all transcripts at once

**Effort:** Medium. Need to define the export schema and handle large datasets.

---

### ~~6. Retry/resume for failed bulk jobs~~ `[suggestion]` ✅ Done (Phase 2)
**Why:** If a background job fails at video 30 of 50, there's no `jobs retry <id>`. You re-run the full command and it skips already-done ones, but re-checks all 30 (slow for large channels).

**Scope:**
- `yt-artist jobs retry <id>` → re-launch with the same command, picking up from where it left off
- The skip-already-done logic already exists; this just needs a convenience command

**Effort:** Small. Mostly CLI wiring.

---

### 7. MCP server: decouple or fix dependency `[suggestion]`
**Why:** The `mcp>=1.0.0` dependency causes `uv` resolution failures because `mcp` requires Python >=3.10 but `requires-python` says >=3.9. The MCP server is optional but it breaks the default install.

**Scope:**
- Option A: Move MCP to a separate package (`yt-artist-mcp`)
- Option B: Change `requires-python` to `>=3.10`
- Option C: Make `mcp` optional dependency that gracefully degrades

**Effort:** Small for option B/C, medium for option A.

---

### ~~21. Pipeline parallelism for bulk summarize~~ `[observed in testing]` ✅ Done (Phase 4)
**Why:** When `summarize --artist-id @X` needs to transcribe missing videos first, it blocks all summarization until every transcript is done. On a 459-video channel (@hubermanlab), this meant Ollama sat idle for 10+ hours while YouTube transcripts trickled in. Running separate transcribe and summarize commands concurrently caused duplicate YouTube requests and 429 rate-limit escalation.

**Scope:**
- In bulk summarize, run transcribe and summarize as a producer-consumer pipeline: transcribe feeds transcripts into the DB, summarize polls for new transcripts and processes them concurrently.
- DB-polling approach (not queue): summarize worker periodically queries for "transcribed but not summarized" videos. Simpler, idempotent, crash-recoverable.
- Split `MAX_CONCURRENCY` budget between transcribe and summarize workers.
- No change to standalone `transcribe` or `summarize` (when all transcripts exist).

**Evidence:** @hubermanlab bulk run (2026-02-10): 341/459 transcripts took ~10h (YouTube throttled after ~300). Summarize was blocked the entire time. With pipeline, first summary would arrive in ~15s instead of hours.

**ADR:** [ADR-0012](adr/0012-pipeline-parallelism.md)

**Effort:** Medium. Polling loop + termination logic + concurrency budget splitting. Existing ThreadPoolExecutor infrastructure reusable.

---

## P2: Quality of Life

### ~~8. Transcript quality scoring (pre-summarize)~~ `[suggestion]` ✅ Done (Session 16)
**Why:** Auto-generated subtitles vary wildly. Music videos produce gibberish. Non-English content misidentified as English produces garbage. Summarizing bad transcripts wastes LLM calls.

**Note:** Session 10 added *summary* quality scoring (`scorer.py`), which assesses how good a summary is after generation. This item is about *transcript* quality scoring — detecting bad transcripts *before* spending LLM calls on summarization. Different problem, complementary solution.

**Scope:**
- Heuristic score: word count, repetition ratio, average word length
- Flag transcripts below threshold: "Warning: transcript for VIDEO may be low quality (score: 0.3/1.0)"
- Optional `--skip-low-quality` flag on summarize

**Effort:** Medium. Needs heuristic tuning and testing.

---

### ~~9. Simplify `build-artist-prompt`~~ `[suggestion]` ✅ Done (Session 15)
**Why:** The DuckDuckGo web search flow is fragile and the dependency is optional. A simpler approach: let users set the "about" text manually.

**Scope:**
- Add `yt-artist set-about --artist-id @X "Huberman Lab covers neuroscience..."`
- Keep `build-artist-prompt` but make it clearly optional/experimental
- The manual path is more reliable and predictable

**Effort:** Small.

---

### ~~10. Rate-limit monitoring~~ `[suggestion]` ✅ Done (Phase 3)
**Why:** Track how many YouTube requests we've made recently. If approaching a threshold, auto-slow-down rather than waiting for 429s.

**Scope:**
- Counter in SQLite: `INSERT INTO request_log (timestamp, type)` on each yt-dlp call
- `yt-artist status` shows: "YouTube requests: 47 in last hour"
- Auto-increase delay when rate is high

**Effort:** Medium. Needs threshold tuning.

---

### ~~22. Timestamped transcripts~~ `[user requested]` ✅ Done (Session 16)
**Why:** Currently `_subs_to_plain_text()` strips all VTT/SRT timestamps — only deduplicated plain text is stored. Timestamps are needed for future features: jump-to-moment links, most-replayed segment correlation, screenshot-at-timestamp, and chapter-aware summarization.

**Scope:**
- Store raw VTT alongside plain text (new `raw_vtt` column on transcripts table, or separate table)
- Parse timestamps into structured format: `[(start_sec, end_sec, text), ...]`
- Optional `--timestamps` flag on transcribe to enable (or always store both)
- Expose via `search-transcripts --with-timestamps` or export

**Effort:** Medium. VTT parsing exists; need schema change, storage method, and CLI surface.

---

### ~~23. cli.py structural refactor: AppContext + _cmd_summarize decomposition~~ `[suggestion]` ✅ Done (Session 18)
**Why:** cli.py is ~1,600 lines with 14 `_cmd_*` functions. Manageable now but `_cmd_summarize` alone is 297 lines mixing 4 concerns (single-video, bulk-sequential, pipeline, scoring setup). Three module-level globals (`_quiet`, `_bg_job_id`, `_bg_storage`) couple helpers to implicit state.

**What was done:**
1. **AppContext dataclass** — replaced 3 module globals + `(args, storage, data_dir)` triple with a single `AppContext` context object threaded through all 15 `_cmd_*` handlers. Deprecated globals kept for backward compatibility.
2. **Decomposed `_cmd_summarize`** — extracted `_summarize_single()`, `_summarize_bulk_sequential()`, `_summarize_pipeline()` as private helpers. `_cmd_summarize` is now ~130 lines of setup + dispatch.
3. **Helper functions updated** — `_hint()` accepts explicit `quiet` param, `_run_bulk()` accepts explicit `job_id`/`job_storage` params instead of reading globals.

**Deferred (only when needed):**
- `commands/` package split — only justified when adding a second entrypoint (API server, TUI) or hitting 2500+ lines.
- `use_cases/` layer — domain logic already lives in fetcher/transcriber/summarizer/scorer/pipeline.

**Note:** ~~Config centralization (config.py) is done (Session 15), but `transcriber.py` line 323 still reads `os.environ.get("YT_ARTIST_PO_TOKEN")` directly — should delegate to `get_youtube_config().po_token`. Fix as standalone cleanup.~~ Fixed in Session 20.

---

### ~~24. Per-video work ledger table~~ `[architecture review]` ✅ Done (Session 19)
Append-only `work_ledger` table recording every transcribe/summarize/score/verify operation with timing, status, model, prompt, strategy, and error info. `WorkTimer` + `record_operation()` in `ledger.py` (best-effort, never breaks operations). Domain functions instrumented. CLI `history` command + ledger counts in `status`.

---

### ~~25. Stop external `storage._conn()` calls~~ `[architecture review]` ✅ Done (Session 20)
Hybrid approach: moved SQL into 11 new Storage methods (`create_job`, `update_job_pid`, `get_job`, `update_job_progress`, `finalize_job`, `mark_job_stale`, `list_recent_jobs`, `delete_old_jobs`, `log_rate_request`, `count_rate_requests`, `get_unscored_transcripts`) for production code; replaced 21 test `_conn()` calls with `transaction()` context manager. Added `JobRow` TypedDict. Zero `_conn()` calls remain outside `storage.py`.

---

### ~~26. FTS5 full-text transcript search~~ `[architecture review]` ✅ Done (Session 22)
**Why:** `search-transcripts` had zero text search — only exact video_id/artist_id filtering. Users with 500+ transcripts couldn't search within transcript text.

**What was done:**
1. **FTS5 virtual table** — `transcripts_fts` content-synced with `transcripts` table (no duplicate storage)
2. **Sync triggers** — `transcripts_ai/ad/au` keep FTS index in sync on INSERT/UPDATE/DELETE
3. **Migration** — `_migrate_fts5_transcripts()` with FTS5 availability check, rebuild from existing transcripts
4. **`search_transcripts()`** Storage method — BM25-ranked results with snippet context via `snippet()` function
5. **CLI** — `--query`/`-q` for FTS5 search, `--limit` for result cap, dual-mode handler (list vs search)
6. **MCP** — `search_transcripts` tool with query/list dual mode
7. **Doctor** — FTS5 availability check added as [7/7]
8. **26 new tests** — storage, migration, trigger sync, CLI, JSON output

---

### ~~27. Persist model/prompt/transcript hashes~~ `[architecture review]` ✅ Done (Session 19)
**Why:** Currently summaries store `model` as a plain string but no hash of the prompt template or transcript content. There's no way to detect "this summary is stale because the prompt changed" or "we should re-summarize because the transcript was updated." Re-summarization today is all-or-nothing.

**What was done:**
1. **hashing.py** — new `content_hash()` utility (SHA-256 hex digest)
2. **Schema** — added `prompt_hash TEXT`, `transcript_hash TEXT` to summaries table + idempotent migration
3. **Storage** — `upsert_summary()` persists hashes; `get_stale_summary_counts()` + `get_stale_video_ids()` for staleness detection
4. **Summarizer** — computes SHA-256 of raw prompt template + raw transcript text at summarization time
5. **CLI** — `--force` (re-summarize all) and `--stale-only` (with --force, only re-summarize stale) flags on `summarize`; `status` shows stale count with breakdown (prompt changed / transcript updated / unknown provenance)
6. Existing summaries get NULL hashes → counted as stale_unknown (correct default)

---

### ~~11. Tech debt: `--_bg-worker` re-execution pattern~~ `[suggestion]` ✅ Done (Session 16)
**Why:** Re-executing the CLI as a subprocess is fragile. If the user's environment changes (PATH, venv activation) between parent and child, the child may fail silently. Works now but should be flagged.

**Scope:**
- Document the known limitation
- Consider: use `sys.executable` (already done) + absolute path to module
- Add a health check: child writes a "started" marker to the log within 5s, parent can verify

**Effort:** Small for docs, medium for health check.

---

## P3: Product Vision

### ~~12. Performance: hot-path language migration~~ `[user requested]` ❌ Won't do (Session 21)
**Why:** Profiling (Session 21) proved Python is not the bottleneck — I/O is. Every CPU-bound function runs for single-digit milliseconds, then waits seconds-to-minutes for yt-dlp subprocess calls, LLM API responses, or SQLite I/O. The heaviest pure-Python function (`_key_term_coverage()` in scorer.py) takes ~15ms, followed by a 5-30s LLM call. Python's `re`, `hashlib`, and `sqlite3` are already C under the hood. Rewriting any module in C/Rust would save tens of milliseconds in a pipeline that takes minutes per video.

**Superseded by:** Items 28, 29, 30 — Python-level algorithmic fixes that deliver real gains.

---

### 28. Fix N+1 query pattern in exporter.py `[profiling]`
**Why:** `export_json()` and `export_csv()` issue 2 DB queries per video (transcript + summaries). For a 2000-video channel, that's ~4000 SQLite round-trips. This is the actual export performance bottleneck.

**Scope:**
- Batch-load transcripts and summaries via JOINs instead of per-video queries
- Chunk by artist or batch of video IDs
- Keep streaming/memory-efficient design (don't load all transcripts at once)

**Effort:** Small-medium. SQL refactor, no new dependencies.

---

### 29. Single-pass transcript quality scoring `[profiling]`
**Why:** `transcript_quality.py` runs 5 separate passes over the transcript text (`split()`, `splitlines()`, char scan, etc.). Could be collapsed into 1-2 passes. Currently ~1-5ms/video — negligible, but cleaner code.

**Scope:**
- Combine `_word_count_score`, `_repetition_ratio_score`, `_avg_word_length_score`, `_punctuation_density_score`, `_line_uniqueness_score` into a single-pass computation
- One `splitlines()`, one word iteration, one char scan

**Effort:** Small. Pure refactor.

---

### 30. Staleness hash caching `[profiling]`
**Why:** `get_stale_summary_counts()` recomputes SHA-256 hashes of prompt templates and transcript text on every call. For 1000 summaries, this takes ~50-200ms. Could cache or compute incrementally.

**Scope:**
- Store precomputed hashes at summarization time (already done for new summaries — issue is legacy summaries with NULL hashes)
- Backfill NULL hashes via a one-time migration or `doctor` command
- Staleness check becomes a pure SQL comparison instead of Python loop + recompute

**Effort:** Small-medium. Migration + storage method update.

---

### 13. Licensing and payment model `[user requested]`
**Why:** If this becomes a consumer product, need a sustainable business model.

**Scope:**
- Free tier: up to 10,000 summaries (lifetime? per month?)
- Paid tier: beyond 50,000 summaries
- Questions to resolve:
  - Per-summary pricing vs subscription
  - Self-hosted (user runs their own Ollama) vs cloud (we run the LLM)
  - How to enforce limits in a CLI tool (honor system? license key? phone-home?)
  - What happens when the free tier runs out (hard block vs degraded?)

**Effort:** Large. Business decision, not just engineering.

---

### 14. Usage metrics tracking `[user requested]`
**Why:** Users want to know their usage, and a payment model requires metering.

**Scope:**
- Counters: summaries generated, transcripts processed, LLM API calls, processing time
- `yt-artist usage` command: show lifetime and last-30-day stats
- Local-only by default (SQLite table). Optional: phone-home for license enforcement.
- Per-artist breakdown: "You've summarized 142 videos for @TED"

**Effort:** Medium. Schema + CLI + aggregation queries.

---

### 15. Shippable product readiness `[user requested]`
**Why:** Currently installable from git clone only. For real adoption, need standard distribution.

**Scope:**
- PyPI publication (`pip install yt-artist`)
- Homebrew tap for Mac (`brew install yt-artist`)
- GitHub Actions CI/CD (test on push, release on tag)
- Semantic versioning (currently 0.1.0)
- `CHANGELOG.md` for release notes

**Effort:** Medium. Standard packaging work.

---

### 16. Public blog post `[user requested]`
**Why:** Document the human-AI collaborative development process for others.

**Scope:**
- Based on `docs/JOURNEY.md` (already written)
- Polish for external audience
- Publish on blog/Medium/dev.to

**Effort:** Small. The draft exists.

---

### 17. Web UI `[explicitly rejected — noting for record]`
**Status:** NOT planned. The CLI is the product. A web UI would be scope creep.

### 18. Multi-user support `[explicitly rejected]`
**Status:** NOT planned. This is a personal tool. SQLite is the right choice.

### 19. Cloud sync `[explicitly rejected]`
**Status:** NOT planned. The SQLite file is the source of truth.

### 20. Video download `[explicitly rejected]`
**Status:** NOT planned. Out of scope and legally risky. We only fetch subtitles.
