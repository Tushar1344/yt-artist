# yt-artist Architecture Diagrams

## 1. High-Level Module Dependency Graph

```
                        +-------------------+
                        |    CLI (cli.py)    |  <-- User entry point
                        |  argparse-based    |
                        |  hints, quickstart |
                        |  --bg, --quiet     |
                        +--------+----------+
                                 |
         +-----------+-----------+-----------+-----------+-----------+
         |           |           |           |           |           |
         v           v           v           v           v           v
  +-----------+ +-----------+ +-----------+ +-----------+ +-----------+ +-----------+
  |fetcher.py | |transcriber| |summarizer | |scorer.py  | | jobs.py   | |yt_dlp_util|
  |yt-dlp     | |.py        | |.py        | |heuristic +| |background | |.py        |
  |--flat-    | |yt-dlp     | |template + | |LLM self-  | |launch,    | |cmd builder|
  | playlist  | |--write-sub| |LLM call   | |check      | |list,stop, | |sleep,delay|
  +-----------+ +-----------+ |strategies | |quality    | |attach,    | |cookies,   |
       |             |        |map-reduce | |scoring    | |retry,     | |concurrency|
       |             |        |refine     | +-----------+ |cleanup    | +-----------+
       |             |        +-----------+      |        +-----------+      |
       |             |             |             |             |             |
       |             |          +--+--+          |             |             |
       |             |          |     |          |             |             |
       v             v          v     v          v             v             |
  +-------------------------------------------+  +----------+               |
  |           storage.py (Storage)            |  | llm.py   |               |
  |  SQLite CRUD: artists, videos,            |  | OpenAI / |               |
  |  transcripts, prompts, summaries, jobs,   |  | Ollama   |               |
  |  request_log. Migrations, WAL mode.       |  | (cached, |               |
  +-------------------------------------------+  |  retry)  |               |
                     |                            +----------+               |
                     v                                                       |
           +-------------------+                                             |
           |   schema.sql      |            Used by transcriber, fetcher ----+
           |   init_db.py      |
           +-------------------+

  +-----------------+     +-------------------+     +-------------------+
  | pipeline.py     |     | rate_limit.py     |     | artist_prompt.py  |
  | 3-stage         |     | request_log table |     | (DuckDuckGo +     |
  | producer-       |     | threshold warnings|     |  LLM about text)  |
  | consumer        |     | auto-cleanup      |     +-------------------+
  | (transcribe ->  |     +-------------------+
  |  summarize ->   |
  |  score)         |     +-------------------+
  +-----------------+     | mcp_server.py     |
                          | (FastMCP wrapper)  |
                          | Same pipeline     |
                          +-------------------+
```

## 2. Data Flow: End-to-End Pipeline

```
  YouTube Channel URL
         |
         v
  +------+-------+     yt-dlp --flat-playlist -j
  |  fetch_channel|----------------------------------+
  +------+-------+                                   |
         |                                           |
         | NDJSON entries                             |
         v                                           v
  +------+-------+                          +--------+-------+
  | Write urllist |                          | Upsert Artist  |
  | markdown file |                          | Upsert Videos  |
  +--------------+                           +--------+-------+
                                                      |
                                                      v
  For each video (parallel, 2 workers):     +--------+-------+
                                            |  transcribe()  |
  +-----------------------------------------+  yt-dlp        |
  |                                         |  --write-sub   |
  |  1. Download subtitles to temp dir      |  --skip-download|
  |     (auto-subs first, manual fallback)  +--------+-------+
  |  2. Parse VTT/SRT -> plain text                  |
  |  3. Deduplicate consecutive lines                |
  |  4. Save to transcripts table                    |
  |  5. (Optional) write .txt file                   |
  |  6. Update ProgressCounter (+ jobs DB if --bg)   |
  +--------------------------------------------------+
                                                      |
                                                      v
  For each video (parallel, 2 workers):     +--------+-------+
                                            |  summarize()   |
  +-----------------------------------------+                |
  |  1. Load transcript from DB             |  Strategy:     |
  |  2. Choose strategy:                    |  auto/truncate/|
  |     - auto (default): single-pass       |  map-reduce/   |
  |       if fits, map-reduce if too long   |  refine        |
  |     - truncate: legacy (cut at limit)   +--------+-------+
  |     - map-reduce: chunk -> map -> reduce         |
  |     - refine: iterative rolling summary          |
  |  3. Load prompt template                         |
  |  4. Fill {artist},{video},{intent},{audience}     |
  |  5. LLM.complete() -> summary (cached client)    |
  |  6. Upsert to summaries table                    |
  |  7. Update ProgressCounter (+ jobs DB if --bg)   |
  +--------------------------------------------------+
                                                      |
                                                      v
  For each summary (sequential):            +--------+-------+
                                            |  score()       |
  +-----------------------------------------+                |
  |  1. Load summary + transcript from DB   |  Heuristic +   |
  |  2. Heuristic score (instant):          |  LLM self-     |
  |     - Length ratio                       |  check         |
  |     - Repetition detection              +--------+-------+
  |     - Key-term coverage                          |
  |     - Structure analysis                         |
  |  3. LLM self-check (1 tiny call):               |
  |     - Rate completeness/coherence/               |
  |       faithfulness 1-5                           |
  |  4. Combined: 0.4*heuristic + 0.6*llm           |
  |  5. Update summaries table with scores           |
  +--------------------------------------------------+
```

## 3. Data Model (Entity Relationship)

```
  +-------------------+       +-------------------+
  |     artists       |       |     prompts       |
  +-------------------+       +-------------------+
  | id (PK, TEXT)     |       | id (PK, TEXT)     |
  | name              |       | name              |
  | channel_url       |       | template          |
  | urllist_path      |       | artist_component  |
  | created_at        |       | video_component   |
  | default_prompt_id-+--FK-->| intent_component  |
  | about             |       | audience_component|
  +--------+----------+       +---------+---------+
           |                            |
           | 1:N                        |
           v                            |
  +--------+----------+                 |
  |     videos        |                 |
  +-------------------+                 |
  | id (PK, TEXT)     |                 |
  | artist_id (FK)    |                 |
  | url               |                 |
  | title             |                 |
  | fetched_at        |                 |
  +--------+----------+                 |
           |                            |
           | 1:1           1:N          |
           v               |            |
  +--------+----------+    |            |
  |   transcripts     |    |            |
  +-------------------+    |            |
  | video_id (PK, FK) |    |            |
  | raw_text          |    |            |
  | format            |    |            |
  | created_at        |    |            |
  +-------------------+    |            |
                           |            |
           +---------------+            |
           |                            |
  +--------+----------+                 |
  |    summaries      |                 |
  +-------------------+                 |
  | id (PK, AUTO)     |                 |
  | video_id (FK)  ---+-- FK to videos  |
  | prompt_id (FK) ---+-- FK to prompts-+
  | content           |
  | created_at        |
  | quality_score     |  <-- 0.0-1.0 combined score
  | heuristic_score   |  <-- 0.0-1.0 instant scoring
  | llm_score         |  <-- 0.0-1.0 LLM self-check
  | UNIQUE(video_id,  |
  |        prompt_id) |
  +-------------------+

  +-------------------+    +-------------------+    +-------------------+
  |   screenshots     |    |   video_stats     |    |      jobs         |
  +-------------------+    +-------------------+    +-------------------+
  | id (PK, AUTO)     |    | video_id (PK, FK) |    | id (PK, TEXT)     |
  | video_id (FK)     |    | view_count        |    | command           |
  | timestamp_sec     |    | most_replayed     |    | status            |
  | transcript_snippet|    +-------------------+    | pid               |
  | file_path         |    (Future tables)          | log_file          |
  +-------------------+                             | started_at        |
                                                    | finished_at       |
                           +-------------------+    | total, done,      |
                           |   request_log     |    | errors            |
                           +-------------------+    | error_message     |
                           | id (PK, AUTO)     |    +-------------------+
                           | timestamp         |
                           | request_type      |
                           +-------------------+
```

## 4. CLI Command Dispatch

```
  yt-artist [--bg] [-q] [--dry-run] <command> [args]
         |
         +-- --bg? --> launch_background() --> detached child --> exit
         |
         +-- --_bg-worker? --> set globals, SIGTERM handler, wrap in crash safety
         |
         v
  +------+------+
  |  argparse   |
  |  dispatcher |
  +------+------+
         |
   +-----+-----+-----+-----+-----+-----+-----+-----+-----+-----+-----+-----+
   |     |     |     |     |     |     |     |     |     |     |     |     |
   v     v     v     v     v     v     v     v     v     v     v     v     v
  fetch  url  trans  sum  score add   list  set   build search quick jobs status
  chan.  list  cribe  mar        prom  prom  def.  art.  trans  start      doctor
              ize    ize        pt    pts   prom  prom  cripts
                                                        |
                                                  +-----+-----+-----+
                                                  |     |     |     |
                                                  v     v     v     v
                                                 list  attach stop  retry
                                                       clean

  Each command handler: _cmd_<name>(args, storage, data_dir)
  After each command: _hint() prints next-step to stderr (unless -q)
```

## 5. Dependency Auto-Creation Chain

```
  summarize(video)
       |
       +-- artist in DB? --NO--> ensure_artist_and_video_for_video_url()
       |                              |
       |                              +-- yt-dlp -j (single video metadata)
       |                              +-- fetch_channel() (full channel)
       |                              +-- upsert artist + all videos
       |
       +-- transcript in DB? --NO--> transcribe()
       |                                 |
       |                                 +-- yt-dlp --write-auto-subs (optimistic)
       |                                 +-- fallback: --write-subs (manual)
       |                                 +-- parse subtitles, dedup lines
       |                                 +-- save_transcript()
       |
       +-- resolve prompt_id
       |      |
       |      +-- --prompt flag? --> use it
       |      +-- artist default? --> use it
       |      +-- env YT_ARTIST_DEFAULT_PROMPT? --> use it
       |      +-- first prompt in DB? --> use it
       |      +-- none? --> SystemExit
       |
       +-- choose strategy (auto/truncate/map-reduce/refine)
       |      |
       |      +-- auto (default): fits context? --> single-pass
       |      |                    too long? --> map-reduce
       |      +-- truncate: cut at MAX_TRANSCRIPT_CHARS
       |      +-- map-reduce: chunk -> summarize each -> combine -> reduce
       |      +-- refine: iterative rolling summary across chunks
       |
       +-- _fill_template() + llm.complete() (cached client, retry)
       +-- upsert_summary()
```

## 6. Background Job Lifecycle

```
  User runs: yt-artist --bg transcribe --artist-id @TED
       |
       v
  PARENT PROCESS:
       |
       +-- Generate job_id (12 hex chars)
       +-- Create log file: data/jobs/<job_id>.log
       +-- INSERT into jobs table (status='running', pid=-1)
       +-- Build child argv:
       |     python -m yt_artist.cli transcribe --artist-id @TED --_bg-worker <job_id>
       +-- subprocess.Popen(start_new_session=True, stdout=log_file)
       +-- UPDATE jobs SET pid = <child_pid>
       +-- Print: "Job a1b2c3d4 launched. Use: yt-artist jobs"
       +-- Exit immediately
       |
       v
  CHILD PROCESS (detached, survives terminal close):
       |
       +-- Set _bg_job_id, _bg_storage globals
       +-- Register SIGTERM handler
       +-- Run _cmd_transcribe() normally
       |     +-- _ProgressCounter ticks update jobs table
       |     +-- Each video: UPDATE jobs SET done=?, errors=?
       +-- On success: finalize_job(status='completed')
       +-- On exception: finalize_job(status='failed', error_message=...)
       +-- On SIGTERM: finalize_job(status='stopped')
       +-- On crash (OOM): PID dies, detected by list_jobs() later
       |
       v
  USER CHECKS LATER:
       |
       +-- yt-artist jobs           --> tabular list with progress
       +-- yt-artist jobs attach id --> tail -f the log file
       +-- yt-artist jobs stop id   --> os.kill(pid, SIGTERM)
       +-- yt-artist jobs retry id  --> re-launch same command
       +-- yt-artist jobs clean     --> remove old finished jobs + logs
```

## 7. Parallel Execution Model

```
  Bulk transcribe --artist-id @TED (50 videos, 23 already done)
       |
       +-- Batch query: SELECT video_id FROM transcripts WHERE ...
       +-- Filter: 27 videos remaining
       +-- maybe_suggest_background(27, "transcribe", ...)
       |     --> stderr: "This will process 27 videos (~4m). Try --bg"
       |
       v
  ThreadPoolExecutor(max_workers=2)
       |
       +-- Worker 1:                    +-- Worker 2:
       |   video_1 -> transcribe()      |   video_2 -> transcribe()
       |   sleep(inter_video_delay)     |   sleep(inter_video_delay)
       |   video_3 -> transcribe()      |   video_4 -> transcribe()
       |   ...                          |   ...
       |                                |
       +-- Both update shared _ProgressCounter (thread-safe via Lock)
       |     +-- counter.tick() increments done/errors
       |     +-- If bg mode: UPDATE jobs SET done=?, errors=?
       |
       v
  All futures complete
       +-- progress.finalize(status='completed')
       +-- Print: "Done: 27/27 (0 errors)"
       +-- _hint(): "Next: summarize --artist-id @TED"
```

## 8. Pipeline Parallelism (3-Stage)

```
  Bulk summarize --artist-id @X (missing transcripts + summaries)
       |
       v
  run_pipeline() in pipeline.py (DB-polling coordination)
       |
       +---------------------------+---------------------------+
       |                           |                           |
       v                           v                           v
  STAGE 1: Transcribe        STAGE 2: Summarize         STAGE 3: Score
  ThreadPoolExecutor(1-2)    ThreadPoolExecutor(1-2)    ThreadPoolExecutor(1)
       |                           |                           |
       | Downloads subtitles       | Polls DB every 5s for     | Polls DB for
       | from YouTube              | "transcribed but not      | "summarized but
       | Writes to DB              |  summarized" videos       |  not scored"
       |                           | Summarizes with strategy  | Heuristic +
       |                           | Writes to DB              | LLM self-check
       |                           |                           | Writes scores
       v                           v                           v
  [DB: transcripts]  ------>  [DB: summaries]  -------->  [DB: quality_score]

  Key design decisions:
  - DB-polling (not queue): simpler, idempotent, crash-recoverable
  - Poller checks every 5s, wakes up in 0.5s increments for responsive shutdown
  - Concurrency budget split: e.g., 2 total -> 1 transcribe + 1 summarize
  - Stage 3 runs single-worker (scoring calls are tiny)
  - Pipeline only activates when bulk summarize finds missing transcripts
  - Standalone transcribe/summarize commands unchanged
```

## 9. Long-Transcript Summarization Strategies

```
  summarize() receives transcript
       |
       +-- len(raw_text) <= MAX_TRANSCRIPT_CHARS (30K)?
       |      |
       |      YES --> Single-pass: one LLM call (all strategies)
       |      |
       |      NO --> Strategy dispatch:
       |
       +-- auto (default):
       |      --> map-reduce (see below)
       |
       +-- truncate:
       |      --> Cut at MAX_TRANSCRIPT_CHARS, single LLM call
       |
       +-- map-reduce:
       |      |
       |      +-- _chunk_text(): split at sentence boundaries (~30K each)
       |      |     +-- Overlap: 500 chars (clamped to chunk_size/2)
       |      |     +-- Boundaries: ". ", "\n", "? ", "! "
       |      |
       |      +-- MAP: summarize each chunk independently (parallelizable)
       |      |     +-- "Summarize section {i} of {n}..."
       |      |     +-- N LLM calls (e.g., 5 for 150K chars)
       |      |
       |      +-- REDUCE: concatenate chunk summaries
       |      |     +-- Fits context? --> one final LLM call
       |      |     +-- Still too long? --> recursive reduce
       |      |
       |      +-- Total: N + 1 LLM calls (+ recursive if needed)
       |
       +-- refine:
              |
              +-- _chunk_text(): same splitting logic
              |
              +-- Chunk 1 --> summarize --> initial summary
              +-- Chunk 2 --> "Previous summary: {prev}\nNew section: {chunk}" --> refined
              +-- Chunk 3 --> "Previous summary: {prev}\nNew section: {chunk}" --> refined
              +-- ...
              +-- Final refined summary is output
              |
              +-- Total: N LLM calls (strictly sequential)
              +-- Best coherence for narrative content
```

## 10. Quality Scoring Architecture

```
  score_summary(summary, transcript, skip_llm=False)
       |
       +-- TIER 1: Heuristic (instant, zero LLM cost)
       |      |
       |      +-- _length_ratio_score():    summary/transcript length  (weight: 0.3)
       |      |     +-- Ideal: 0.02-0.10
       |      |     +-- Too short = under-summarized, too long = regurgitation
       |      |
       |      +-- _key_term_coverage():     top-N terms from transcript (weight: 0.3)
       |      |     +-- Extract frequent words, check % in summary
       |      |
       |      +-- _repetition_score():      duplicate sentence detection (weight: 0.2)
       |      |     +-- High repetition = model looping
       |      |
       |      +-- _structure_score():       multi-sentence, bullets     (weight: 0.2)
       |      |     +-- Single-line for 2hr episode is suspect
       |      |
       |      +-- heuristic_score = weighted average --> 0.0-1.0
       |
       +-- skip_llm? --> quality_score = heuristic_score (done)
       |
       +-- TIER 2: LLM self-check (1 tiny call)
       |      |
       |      +-- Prompt: "Rate this summary 1-5 for:
       |      |     - Completeness, Coherence, Faithfulness
       |      |     Return three numbers, e.g.: 4 3 5"
       |      |
       |      +-- _parse_llm_rating(): extract 3 integers
       |      +-- llm_score = average / 5 --> 0.0-1.0
       |      +-- On LLM failure: fall back to heuristic-only
       |
       +-- quality_score = 0.4 * heuristic + 0.6 * llm --> 0.0-1.0
       |
       +-- Store: UPDATE summaries SET quality_score=?, heuristic_score=?, llm_score=?
```

## 11. Connection Management Pattern

```
  Every Storage method:

    def some_method(self, ...):
        conn = self._conn()        # New connection per call
        try:                        # WAL mode enables concurrent readers
            conn.execute(...)
            conn.commit()           # Commit per call
        finally:
            conn.close()            # Close immediately

  Result: N methods = N connections opened/closed
  WAL mode: foreground reads while background writes (jobs progress)
```

## 12. LLM Client Decision Tree

```
  get_client()  (cached: reused across summarize calls)
       |
       +-- OPENAI_BASE_URL set?
       |      |
       |      +-- points to Ollama (port 11434)?
       |      |      --> api_key = "ollama", use that URL
       |      |
       |      +-- other URL?
       |             --> use OPENAI_API_KEY as-is
       |
       +-- OPENAI_BASE_URL not set?
              |
              +-- OPENAI_API_KEY set?
              |      --> base_url = api.openai.com/v1
              |
              +-- OPENAI_API_KEY not set?
                     --> base_url = localhost:11434/v1 (Ollama)
                     --> api_key = "ollama"

  Model selection:
       OPENAI_MODEL env > model param > default
       default = "mistral" (Ollama) | "gpt-4o-mini" (OpenAI)

  Client cache invalidated when env vars change between calls.
  Retry with exponential backoff on transient LLM failures.
```

## 13. Onboarding Flow

```
  User runs any command (first time, empty DB)
       |
       +-- is_first_run()? (no artists in DB)
       |      |
       |      YES --> stderr: "First time? Try: yt-artist quickstart"
       |
       +-- Execute command
       |
       +-- --quiet set? --> skip hints
       |
       +-- Print context-aware hint to stderr:
              |
              +-- fetch-channel --> "Next: yt-artist transcribe --artist-id @X"
              +-- transcribe (single) --> "Next: yt-artist summarize <video_id>"
              +-- transcribe (bulk) --> "Next: yt-artist summarize --artist-id @X"
              +-- summarize (single) --> "Try bulk: summarize --artist-id @X"
              +-- summarize (bulk) --> "Next: yt-artist score --artist-id @X"
              +-- score --> "Check: yt-artist status"
              +-- add-prompt --> "Next: set-default-prompt --prompt <id>"
              +-- set-default-prompt --> "Next: summarize --artist-id @X"

  Hints include real data: actual artist IDs, video IDs, counts.
  All hints go to stderr. stdout is clean for piping.
```

## 14. Rate-Limit Safety Stack

```
  Layer 1: yt-dlp flags (per request)
       +-- --sleep-requests 1.5s   (YT_ARTIST_SLEEP_REQUESTS)
       +-- --sleep-subtitles 2s    (YT_ARTIST_SLEEP_SUBTITLES)

  Layer 2: Inter-video delay (per video in bulk)
       +-- time.sleep(2.0s)        (YT_ARTIST_INTER_VIDEO_DELAY)

  Layer 3: Concurrency cap (parallel workers)
       +-- max_workers = 2          (YT_ARTIST_MAX_CONCURRENCY)

  Layer 4: Subtitle strategy (reduce requests)
       +-- Try auto-subs first (most videos have these)
       +-- Fallback to manual subs only if auto fails

  Layer 5: HTTP 429 backoff (per request)
       +-- Exponential backoff: 5s -> 10s -> 20s -> 60s cap
       +-- Max 3 retries before aborting

  Layer 6: Rate-limit monitoring (request_log table)
       +-- Log every yt-dlp request with timestamp
       +-- Warn at 200/hr and 400/hr thresholds
       +-- Auto-cleanup logs older than 24h
       +-- `yt-artist status` shows request counts

  Layer 7: Cookie support (avoid auth failures, higher rate limits)
       +-- YT_ARTIST_COOKIES_BROWSER=chrome
       +-- YT_ARTIST_COOKIES_FILE=/path/to/cookies.txt
       +-- Strongly recommended for 50+ video bulk transcription

  All layers configurable via environment variables.
  Defaults are conservative: safe for 500+ video channels.
```
