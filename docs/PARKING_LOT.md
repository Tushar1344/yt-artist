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

### 1. Input validation and safe-fail on bad YouTube URLs `[suggestion]`
**Why:** Right now, malformed URLs, private/deleted videos, and channels with zero public videos produce cryptic yt-dlp stderr errors. New users will hit this in the first 5 minutes.

**Scope:**
- Validate URL format before calling yt-dlp (is it even a YouTube URL?)
- Detect private/deleted videos from yt-dlp exit codes → clear error message
- Detect empty channels (zero videos) → suggest checking the URL
- Detect age-restricted videos without cookies → suggest `YT_ARTIST_COOKIES_BROWSER`

**Effort:** Small. Mostly wrapping existing yt-dlp calls with better error handling.

---

### 2. `--dry-run` for bulk operations `[suggestion]`
**Why:** Users are nervous about running `transcribe --artist-id @channel` on 500 videos. They want to see what will happen before committing.

**Scope:**
- `yt-artist --dry-run transcribe --artist-id @X` → "Would transcribe 47 videos (23 already done, 24 remaining). Estimated: ~4m"
- `yt-artist --dry-run summarize --artist-id @X` → "Would summarize 31 videos (16 already done, 15 remaining). Estimated: ~4m"
- No actual work performed. Exit code 0.

**Effort:** Small. The batch-query logic already exists; this just skips the execution.

---

### 3. Security: warn about unencrypted DB in docs `[suggestion]`
**Why:** The SQLite file contains all transcripts and summaries in plaintext. If users are summarizing sensitive content (corporate training, private channels), this is a data exposure risk. Users should know.

**Scope:**
- Add a "Security considerations" section to USER_GUIDE.md
- Mention: DB is unencrypted, cookie files contain session tokens, `.gitignore` already excludes both
- Do NOT implement encryption — just inform users

**Effort:** Tiny. Documentation only.

---

## P1: High-Value Features

### 4. `status` command `[suggestion]`
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

### 6. Retry/resume for failed bulk jobs `[suggestion]`
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

### 21. Pipeline parallelism for bulk summarize `[observed in testing]`
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

### 8. Transcript quality scoring `[suggestion]`
**Why:** Auto-generated subtitles vary wildly. Music videos produce gibberish. Non-English content misidentified as English produces garbage. Summarizing bad transcripts wastes LLM calls.

**Scope:**
- Heuristic score: word count, repetition ratio, average word length
- Flag transcripts below threshold: "Warning: transcript for VIDEO may be low quality (score: 0.3/1.0)"
- Optional `--skip-low-quality` flag on summarize

**Effort:** Medium. Needs heuristic tuning and testing.

---

### 9. Simplify `build-artist-prompt` `[suggestion]`
**Why:** The DuckDuckGo web search flow is fragile and the dependency is optional. A simpler approach: let users set the "about" text manually.

**Scope:**
- Add `yt-artist set-about --artist-id @X "Huberman Lab covers neuroscience..."`
- Keep `build-artist-prompt` but make it clearly optional/experimental
- The manual path is more reliable and predictable

**Effort:** Small.

---

### 10. Rate-limit monitoring `[suggestion]`
**Why:** Track how many YouTube requests we've made recently. If approaching a threshold, auto-slow-down rather than waiting for 429s.

**Scope:**
- Counter in SQLite: `INSERT INTO request_log (timestamp, type)` on each yt-dlp call
- `yt-artist status` shows: "YouTube requests: 47 in last hour"
- Auto-increase delay when rate is high

**Effort:** Medium. Needs threshold tuning.

---

### 11. Tech debt: `--_bg-worker` re-execution pattern `[suggestion]`
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
