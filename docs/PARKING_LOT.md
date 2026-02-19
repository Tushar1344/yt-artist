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

### 5. Export/backup `[suggestion]`
**Why:** Users who invest hours transcribing 500 videos want to know their data isn't locked in a SQLite file they can't read. This is a trust issue that blocks adoption.

**Scope:**
- `yt-artist export --artist-id @X --format json` → all videos + transcripts + summaries
- `yt-artist export --artist-id @X --format csv` → flat table
- Export to stdout (pipeable) or `--output file.json`

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

### 23. cli.py structural refactor: AppContext + _cmd_summarize decomposition `[suggestion]`
**Why:** cli.py is ~1,600 lines with 14 `_cmd_*` functions. Manageable now but `_cmd_summarize` alone is 297 lines mixing 4 concerns (single-video, bulk-sequential, pipeline, scoring setup). Three module-level globals (`_quiet`, `_bg_job_id`, `_bg_storage`) couple helpers to implicit state.

**Note:** Config centralization (config.py) is done (Session 15), but `transcriber.py` line 323 still reads `os.environ.get("YT_ARTIST_PO_TOKEN")` directly — should delegate to `get_youtube_config().po_token`. Fix as part of AppContext work or standalone cleanup.

**Recommended approach (2 steps):**
1. **AppContext dataclass** — replace the 3 globals + `(args, storage, data_dir)` triple with a single context object. Prerequisite for any further splitting. Improves testability immediately.
2. **Break up `_cmd_summarize`** — extract `_summarize_single()`, `_summarize_bulk_sequential()`, `_summarize_pipeline()` as private helpers. Same file, just cleaner.

**Deferred (only when needed):**
- `commands/` package split — only justified when adding a second entrypoint (API server, TUI) or hitting 2500+ lines. mcp_server.py already imports domain modules directly (zero coupling to cli.py).
- `use_cases/` layer — domain logic already lives in fetcher/transcriber/summarizer/scorer/pipeline. Adding a third layer between cli and domain has little benefit today.

**Effort:** Medium. AppContext is mechanical but touches all 14 commands + helpers. _cmd_summarize decomposition is contained. Test disruption is moderate (many tests patch sys.argv + call main()).

---

### 24. Per-video work ledger table `[architecture review]`
**Why:** No history of *what* was done to each video and *when*. If a summarization fails, was retried, or produced different results with a different model, there's no audit trail. The current schema only stores the latest state (transcript exists or not, summary exists or not).

**Scope:**
- New `work_ledger` table: `(id, video_id, operation, model, prompt_id, started_at, finished_at, status, error_message)`
- Operations: `transcribe`, `summarize`, `score`, `verify`
- Enables: retry-with-backoff intelligence, cost tracking per operation, "what changed" debugging
- CLI: `yt-artist history --video-id X` → show all operations for a video

**Effort:** Medium. Schema + storage methods + CLI command + backfill question (populate from existing data?).

---

### 25. Stop external `storage._conn()` calls `[architecture review]`
**Why:** `_conn()` is a private method but `jobs.py` (8 calls) and `rate_limit.py` (2 calls) use it directly, bypassing context managers (`_read_conn()`, `_write_conn()`, `transaction()`). Tests have 26 direct calls. This couples callers to connection lifecycle details and makes it harder to add connection pooling or tracing later.

**Current state (partial):** Context managers exist and most storage.py methods use them internally. The problem is external callers.

**Scope:**
- `jobs.py`: expose proper public methods on Storage (or a JobsStore helper) for each operation
- `rate_limit.py`: expose `log_request()` and `get_request_counts()` on Storage
- Tests: migrate from `store._conn()` to public methods or use `transaction()` context manager
- Consider: make `_conn()` raise DeprecationWarning when called from outside storage.py

**Effort:** Medium. Mechanical but touches many files. Jobs.py is the bulk of the work.

---

### 26. FTS5 full-text transcript search `[architecture review]`
**Why:** `search-transcripts` currently does exact video_id/artist_id filtering only. There's no way to search *within* transcript text (e.g., "find all videos where the speaker mentions dopamine"). This is a significant UX gap for users with large transcript libraries.

**Scope:**
- FTS5 virtual table: `CREATE VIRTUAL TABLE transcripts_fts USING fts5(raw_text, content=transcripts, content_rowid=rowid)`
- Triggers to keep FTS index in sync on INSERT/UPDATE/DELETE
- `search-transcripts --query "dopamine"` → ranked results with snippet context
- Migration: rebuild FTS index from existing transcripts on schema upgrade
- `--json` support for search results

**Effort:** Medium. FTS5 is well-documented SQLite feature. Main work: migration, index sync triggers, snippet extraction, CLI surface.

---

### 27. Persist model/prompt/transcript hashes `[architecture review]`
**Why:** Currently summaries store `model` as a plain string but no hash of the prompt template or transcript content. There's no way to detect "this summary is stale because the prompt changed" or "we should re-summarize because the transcript was updated." Re-summarization today is all-or-nothing.

**Scope:**
- New columns on `summaries` table: `prompt_hash TEXT`, `transcript_hash TEXT`
- Hash: SHA-256 of prompt template text and transcript content at summarization time
- `summarize --force` could check hashes: "3 summaries are stale (prompt changed), 2 stale (transcript updated)"
- `status` command: show stale summary count
- Migration: existing summaries get NULL hashes (unknown provenance)

**Effort:** Medium. Schema change + hash computation at summarize time + staleness detection logic.

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

### 12. Performance: hot-path language migration `[user requested]`
**Why:** For very large channels (5000+ videos), Python may become the bottleneck for subtitle parsing, VTT deduplication, or batch DB operations.

**Scope:**
- Profile the actual bottlenecks (likely: yt-dlp subprocess overhead, not Python itself)
- Candidates for Rust/C extension: VTT parser, transcript deduplication, large batch INSERT
- Consider: is the bottleneck actually I/O (network, disk) rather than CPU?

**Effort:** Large. Only worth doing after profiling proves Python is the bottleneck.

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
