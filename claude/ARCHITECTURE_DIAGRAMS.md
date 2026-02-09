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
         +-----------+-----------+-----------+-----------+
         |           |           |           |           |
         v           v           v           v           v
  +-----------+ +-----------+ +-----------+ +-----------+ +-----------+
  |fetcher.py | |transcriber| |summarizer | | jobs.py   | |yt_dlp_util|
  |yt-dlp     | |.py        | |.py        | |background | |.py        |
  |--flat-    | |yt-dlp     | |template + | |launch,    | |cmd builder|
  | playlist  | |--write-sub| |LLM call   | |list,stop, | |sleep,delay|
  +-----------+ +-----------+ +-----------+ |attach,    | |cookies,   |
       |             |             |        |cleanup    | |concurrency|
       |             |             |        +-----------+ +-----------+
       |             |          +--+--+          |             |
       |             |          |     |          |             |
       v             v          v     v          v             |
  +-------------------------------------------+  +----------+ |
  |           storage.py (Storage)            |  | llm.py   | |
  |  SQLite CRUD: artists, videos,            |  | OpenAI / | |
  |  transcripts, prompts, summaries, jobs    |  | Ollama   | |
  |  Migrations, WAL mode                     |  | (cached) | |
  +-------------------------------------------+  +----------+ |
                     |                                         |
                     v                                         |
           +-------------------+                               |
           |   schema.sql      |          Used by transcriber--+
           |   init_db.py      |          and fetcher
           +-------------------+

  +-----------------+          +-------------------+
  | mcp_server.py   |          | artist_prompt.py  |
  | (FastMCP wrapper)|          | (DuckDuckGo +     |
  | Same pipeline   |          |  LLM about text)  |
  +-----------------+          +-------------------+
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
  |  1. Load transcript from DB             |  Fill template |
  |  2. Truncate if > context window        |  Call LLM      |
  |  3. Load prompt template                |  Save summary  |
  |  4. Fill {artist},{video},{intent},     +--------+-------+
  |     {audience} placeholders                      |
  |  5. System prompt = filled template              |
  |  6. User content = "Transcript:\n\n" + text      |
  |  7. LLM.complete() -> summary (cached client)    |
  |  8. Upsert to summaries table                    |
  |  9. Update ProgressCounter (+ jobs DB if --bg)   |
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
                                                    | total, done,      |
                                                    | errors            |
                                                    | error_message     |
                                                    +-------------------+
```

## 4. CLI Command Dispatch

```
  yt-artist [--bg] [-q] <command> [args]
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
   +-----+-----+-----+-----+-----+-----+-----+-----+-----+-----+
   |     |     |     |     |     |     |     |     |     |     |
   v     v     v     v     v     v     v     v     v     v     v
  fetch  url  trans  sum  add   list  set   build search quick jobs
  chan.  list  cribe  mar  prom  prom  def.  art.  trans  start
              ize    ize  pt    pts   prom  prom  cripts
                                                         |
                                                   +-----+-----+
                                                   |     |     |
                                                   v     v     v
                                                  list  attach stop
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
       +-- truncate transcript if needed
       +-- _fill_template() + llm.complete() (cached client)
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

## 8. Connection Management Pattern

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

## 9. LLM Client Decision Tree

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
```

## 10. Onboarding Flow

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
              +-- summarize (bulk) --> "Next: yt-artist search-transcripts"
              +-- add-prompt --> "Next: set-default-prompt --prompt <id>"
              +-- set-default-prompt --> "Next: summarize --artist-id @X"

  Hints include real data: actual artist IDs, video IDs, counts.
  All hints go to stderr. stdout is clean for piping.
```

## 11. Rate-Limit Safety Stack

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

  Layer 5: Cookie support (avoid auth failures)
       +-- YT_ARTIST_COOKIES_BROWSER=chrome
       +-- YT_ARTIST_COOKIES_FILE=/path/to/cookies.txt

  All layers configurable via environment variables.
  Defaults are conservative: safe for 500+ video channels.
```
