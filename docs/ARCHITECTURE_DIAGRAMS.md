# yt-artist Architecture Diagrams

## 1. High-Level Module Dependency Graph

```mermaid
graph TD
    CLI["CLI (cli.py)<br/>argparse, hints, quickstart<br/>--bg, --quiet, --verify"]

    subgraph Core Modules
        FETCH["fetcher.py<br/>yt-dlp --flat-playlist"]
        TRANS["transcriber.py<br/>yt-dlp --write-sub"]
        SUMM["summarizer.py<br/>strategies: map-reduce, refine"]
        SCORE["scorer.py<br/>heuristic + LLM self-check<br/>entity verify + faithfulness"]
        JOBS["jobs.py<br/>background launch/list<br/>stop/attach/retry/clean"]
        YTDLP["yt_dlp_util.py<br/>cmd builder, sleep<br/>delay, cookies, concurrency"]
    end

    subgraph BAML Prompt Layer
        PROMPTS["prompts.py<br/>BAML adapter (6 functions)"]
        BAMLCLIENT["baml_client/<br/>(auto-generated)"]
        BAMLSRC["baml_src/*.baml<br/>(git-versioned)"]
    end

    subgraph Data Layer
        STORAGE["storage.py<br/>SQLite CRUD, migrations, WAL"]
        SCHEMA["schema.sql + init_db.py"]
    end

    subgraph Support Modules
        PIPELINE["pipeline.py<br/>3-stage producer-consumer"]
        RATE["rate_limit.py<br/>request_log, thresholds"]
        ARTIST["artist_prompt.py<br/>DuckDuckGo + LLM about"]
        MCP["mcp_server.py<br/>FastMCP wrapper"]
        LLM["llm.py<br/>OpenAI / Ollama<br/>cached client, retry"]
    end

    CLI --> FETCH
    CLI --> TRANS
    CLI --> SUMM
    CLI --> SCORE
    CLI --> JOBS
    CLI --> YTDLP

    SUMM --> PROMPTS
    SCORE --> PROMPTS
    PROMPTS --> BAMLCLIENT
    BAMLSRC -- "baml-cli generate" --> BAMLCLIENT

    FETCH --> STORAGE
    TRANS --> STORAGE
    SUMM --> STORAGE
    SCORE --> STORAGE
    JOBS --> STORAGE
    STORAGE --> SCHEMA

    SUMM --> LLM
    SCORE --> LLM

    TRANS --> YTDLP
    FETCH --> YTDLP

    PIPELINE --> TRANS
    PIPELINE --> SUMM
    PIPELINE --> SCORE
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
  |  5. BAML prompt via prompts.py (cached client)   |
  |  6. Upsert to summaries table                    |
  |  7. Update ProgressCounter (+ jobs DB if --bg)   |
  +--------------------------------------------------+
                                                      |
                                                      v
  For each summary (parallel, N workers):   +--------+-------+
                                            |  score()       |
  +-----------------------------------------+                |
  |  1. Load summary + transcript from DB   |  Heuristic +   |
  |  2. Heuristic score (instant, 5 subs):  |  LLM self-     |
  |     - Length ratio         (0.25)        |  check +       |
  |     - Repetition detection (0.15)        |  entity verify |
  |     - Key-term coverage    (0.25)        +--------+-------+
  |     - Structure analysis   (0.15)                 |
  |     - Named entity verify  (0.20)                 |
  |  3. LLM self-check (1 call, BAML typed):          |
  |     - ScoreSummary -> ScoreRating                  |
  |     - completeness, coherence, faithfulness 1-5    |
  |     - Uses _sample_transcript (start/mid/end)      |
  |     - faithfulness tracked separately in DB        |
  |  4. Combined: 0.4*heuristic + 0.6*llm             |
  |  5. (Optional) --verify: claim verification        |
  |     - VerifyClaims BAML -> ClaimVerification[]     |
  |     - 1 extra LLM call, verification_score in DB   |
  |  6. Update summaries table with all scores         |
  +----------------------------------------------------+
```

## 3. Data Model (Entity Relationship)

```mermaid
erDiagram
    artists {
        TEXT id PK
        TEXT name
        TEXT channel_url
        TEXT urllist_path
        TEXT created_at
        TEXT default_prompt_id FK
        TEXT about
    }

    videos {
        TEXT id PK
        TEXT artist_id FK
        TEXT url
        TEXT title
        TEXT fetched_at
    }

    transcripts {
        TEXT video_id PK "FK to videos"
        TEXT raw_text
        TEXT format
        TEXT created_at
    }

    prompts {
        TEXT id PK
        TEXT name
        TEXT template
        TEXT artist_component
        TEXT video_component
        TEXT intent_component
        TEXT audience_component
    }

    summaries {
        INTEGER id PK "AUTOINCREMENT"
        TEXT video_id FK
        TEXT prompt_id FK
        TEXT content
        TEXT created_at
        REAL quality_score "0.0-1.0 combined"
        REAL heuristic_score "0.0-1.0 instant"
        REAL llm_score "0.0-1.0 LLM self-check"
        REAL faithfulness_score "0.0-1.0 from LLM rating"
        REAL verification_score "0.0-1.0 from --verify"
    }

    screenshots {
        INTEGER id PK "AUTOINCREMENT"
        TEXT video_id FK
        REAL timestamp_sec
        TEXT transcript_snippet
        TEXT file_path
    }

    video_stats {
        TEXT video_id PK "FK to videos"
        INTEGER view_count
        TEXT most_replayed
    }

    jobs {
        TEXT id PK
        TEXT command
        TEXT status
        INTEGER pid
        TEXT log_file
        TEXT started_at
        TEXT finished_at
        INTEGER total
        INTEGER done
        INTEGER errors
        TEXT error_message
    }

    request_log {
        INTEGER id PK "AUTOINCREMENT"
        TEXT timestamp
        TEXT request_type
    }

    artists ||--o{ videos : "has"
    artists }o--o| prompts : "default_prompt_id"
    videos ||--o| transcripts : "has"
    videos ||--o{ summaries : "has"
    videos ||--o{ screenshots : "has (future)"
    videos ||--o| video_stats : "has (future)"
    prompts ||--o{ summaries : "used by"
```

Indexes: `idx_videos_artist_id`, `idx_summaries_video_id`, `idx_summaries_prompt_id`, `idx_screenshots_video_id`, `idx_jobs_status`, `idx_request_log_timestamp`. Summaries has `UNIQUE(video_id, prompt_id)`.

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

  Notable flags:
    score --verify    Run claim verification (1 extra LLM call per summary)
    score --skip-llm  Heuristic-only scoring (zero LLM calls)
```

## 5. Dependency Auto-Creation Chain

```mermaid
flowchart TD
    START["summarize(video)"]

    HAS_ARTIST{"Artist in DB?"}
    ENSURE["ensure_artist_and_video_for_video_url()<br/>yt-dlp -j → fetch_channel()<br/>upsert artist + all videos"]

    HAS_TRANSCRIPT{"Transcript in DB?"}
    TRANSCRIBE["transcribe()<br/>yt-dlp --write-auto-subs<br/>fallback: --write-subs<br/>parse + dedup → save_transcript()"]

    RESOLVE_PROMPT{"Resolve prompt_id"}
    PROMPT_FLAG["--prompt flag"]
    ARTIST_DEFAULT["Artist default"]
    ENV_DEFAULT["env YT_ARTIST_DEFAULT_PROMPT"]
    FIRST_DB["First prompt in DB"]
    NO_PROMPT["SystemExit: no prompt"]

    CHOOSE_STRATEGY{"Choose strategy"}
    AUTO["auto: fits → single-pass<br/>too long → map-reduce"]
    TRUNCATE["truncate: cut at limit"]
    MAPREDUCE["map-reduce: chunk → map → reduce"]
    REFINE["refine: iterative rolling summary"]

    EXECUTE["_fill_template() + llm.complete()<br/>→ upsert_summary()"]

    START --> HAS_ARTIST
    HAS_ARTIST -- "NO" --> ENSURE --> HAS_TRANSCRIPT
    HAS_ARTIST -- "YES" --> HAS_TRANSCRIPT
    HAS_TRANSCRIPT -- "NO" --> TRANSCRIBE --> RESOLVE_PROMPT
    HAS_TRANSCRIPT -- "YES" --> RESOLVE_PROMPT

    RESOLVE_PROMPT --> PROMPT_FLAG
    PROMPT_FLAG -- "not set" --> ARTIST_DEFAULT
    ARTIST_DEFAULT -- "not set" --> ENV_DEFAULT
    ENV_DEFAULT -- "not set" --> FIRST_DB
    FIRST_DB -- "not set" --> NO_PROMPT

    PROMPT_FLAG -- "found" --> CHOOSE_STRATEGY
    ARTIST_DEFAULT -- "found" --> CHOOSE_STRATEGY
    ENV_DEFAULT -- "found" --> CHOOSE_STRATEGY
    FIRST_DB -- "found" --> CHOOSE_STRATEGY

    CHOOSE_STRATEGY --> AUTO
    CHOOSE_STRATEGY --> TRUNCATE
    CHOOSE_STRATEGY --> MAPREDUCE
    CHOOSE_STRATEGY --> REFINE

    AUTO --> EXECUTE
    TRUNCATE --> EXECUTE
    MAPREDUCE --> EXECUTE
    REFINE --> EXECUTE
```

## 6. Background Job Lifecycle

```mermaid
flowchart TD
    USER["yt-artist --bg transcribe --artist-id @TED"]

    subgraph Parent["PARENT PROCESS"]
        P1["Generate job_id (12 hex chars)"]
        P2["Create log: data/jobs/job_id.log"]
        P3["INSERT jobs (status=running, pid=-1)"]
        P4["subprocess.Popen(start_new_session=True)"]
        P5["UPDATE jobs SET pid=child_pid"]
        P6["Print: Job launched. Use: yt-artist jobs"]
        P7["Exit immediately"]
        P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7
    end

    subgraph Child["CHILD PROCESS (detached)"]
        C1["Set _bg_job_id, _bg_storage globals"]
        C2["Register SIGTERM handler"]
        C3["Run _cmd_transcribe() normally<br/>ProgressCounter → UPDATE jobs"]
        C1 --> C2 --> C3

        C3 --> SUCCESS["finalize(status=completed)"]
        C3 --> EXCEPTION["finalize(status=failed,<br/>error_message=...)"]
        C3 --> SIGTERM["finalize(status=stopped)"]
        C3 --> CRASH["PID dies (OOM)<br/>detected by list_jobs()"]
    end

    subgraph Monitor["USER CHECKS LATER"]
        M1["yt-artist jobs → tabular list"]
        M2["jobs attach id → tail -f log"]
        M3["jobs stop id → os.kill SIGTERM"]
        M4["jobs retry id → re-launch"]
        M5["jobs clean → remove old jobs"]
    end

    USER --> Parent
    P4 -- "fork" --> Child
    Child --> Monitor
```

## 7. Parallel Execution Model

```
  Bulk transcribe/summarize/score --artist-id @TED
       |
       +-- Batch query: find remaining items to process
       +-- Filter: N items remaining
       +-- maybe_suggest_background(N, "command", ...)
       |     --> stderr: "This will process N items (~Xm). Try --bg"
       |
       v
  ThreadPoolExecutor(max_workers=concurrency)     # --concurrency flag, default 2
       |
       +-- Worker 1:                    +-- Worker 2:
       |   item_1 -> process()          |   item_2 -> process()
       |   sleep(inter_video_delay)     |   sleep(inter_video_delay)
       |   item_3 -> process()          |   item_4 -> process()
       |   ...                          |   ...
       |                                |
       +-- Both update shared _ProgressCounter (thread-safe via Lock)
       |     +-- counter.tick() increments done/errors
       |     +-- If bg mode: UPDATE jobs SET done=?, errors=?
       |
       v
  All futures complete
       +-- progress.finalize(status='completed')
       +-- Print: "Done: N/N (0 errors)"
       +-- _hint(): context-aware next step

  All bulk commands use this pattern: transcribe, summarize, score.
  Score additionally prints per-item detail (quality, faithfulness, verification).

  Nested parallelism in map-reduce:
       +-- Outer: bulk summarize workers (max_workers=2)
       +-- Inner: map-reduce chunk workers (max_workers=_MAP_CONCURRENCY=3)
       +-- Total threads bounded: outer × inner = 6 max
```

## 8. Pipeline Parallelism (3-Stage)

```mermaid
flowchart LR
    INPUT["Bulk summarize --artist-id @X<br/>(missing transcripts + summaries)"]
    PIPELINE["run_pipeline()<br/>DB-polling coordination"]

    subgraph S1["Stage 1: Transcribe"]
        T_EXEC["ThreadPoolExecutor(1-2)"]
        T_WORK["Download subtitles<br/>from YouTube"]
        T_DB[("DB: transcripts")]
        T_EXEC --> T_WORK --> T_DB
    end

    subgraph S2["Stage 2: Summarize"]
        S_EXEC["ThreadPoolExecutor(1-2)"]
        S_POLL["Poll DB every 5s for<br/>transcribed-not-summarized"]
        S_DB[("DB: summaries")]
        S_EXEC --> S_POLL --> S_DB
    end

    subgraph S3["Stage 3: Score"]
        SC_EXEC["ThreadPoolExecutor(1)"]
        SC_POLL["Poll DB for<br/>summarized-not-scored"]
        SC_DB[("DB: quality_score")]
        SC_EXEC --> SC_POLL --> SC_DB
    end

    INPUT --> PIPELINE
    PIPELINE --> S1
    PIPELINE --> S2
    PIPELINE --> S3
    T_DB -. "triggers" .-> S_POLL
    S_DB -. "triggers" .-> SC_POLL
```

Key design decisions:
- DB-polling (not queue): simpler, idempotent, crash-recoverable
- Poller checks every 5s, wakes up in 0.5s increments for responsive shutdown
- Concurrency budget split: e.g., 2 total → 1 transcribe + 1 summarize
- Stage 3 runs single-worker (scoring calls are tiny)
- Pipeline only activates when bulk summarize finds missing transcripts
- Standalone transcribe/summarize commands unchanged

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
       |      +-- MAP: summarize each chunk in parallel (ThreadPoolExecutor)
       |      |     +-- "Summarize section {i} of {n}..."
       |      |     +-- N LLM calls (e.g., 5 for 150K chars)
       |      |     +-- Concurrency: _MAP_CONCURRENCY (default 3, env YT_ARTIST_MAP_CONCURRENCY)
       |      |     +-- Results reassembled in original chunk order
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
  score_summary(summary, transcript, skip_llm=False, verify=False)
       |
       +-- HEURISTIC (instant, zero LLM cost)
       |      |
       |      +-- _length_ratio_score():    summary/transcript length  (weight: 0.25)
       |      |     +-- Ideal: 0.02-0.10
       |      |     +-- Too short = under-summarized, too long = regurgitation
       |      |
       |      +-- _repetition_score():      duplicate sentence detection (weight: 0.15)
       |      |     +-- High repetition = model looping
       |      |
       |      +-- _key_term_coverage():     top-N terms from transcript (weight: 0.25)
       |      |     +-- Extract frequent words, check % in summary
       |      |
       |      +-- _structure_score():       multi-sentence, bullets     (weight: 0.15)
       |      |     +-- Single-line for 2hr episode is suspect
       |      |
       |      +-- _named_entity_score():    proper noun verification    (weight: 0.20)
       |      |     +-- Regex-extract multi-word names ("Elijah Wood", "Stanford")
       |      |     +-- Filter _ENTITY_STOPWORDS (months, days, sentence-start words)
       |      |     +-- Check each entity against transcript (case-insensitive)
       |      |     +-- Return verified_count / total (1.0 if no entities)
       |      |     +-- Hallucinated name → entity score ≈ 0.0 → pulls heuristic down
       |      |
       |      +-- heuristic_score = weighted average --> 0.0-1.0
       |
       +-- skip_llm? --> quality_score = heuristic_score (done)
       |
       +-- LLM SELF-CHECK (1 call via BAML ScoreSummary)
       |      |
       |      +-- _sample_transcript(transcript, max_excerpt=3000):
       |      |     +-- Short (≤ 3000): return whole text
       |      |     +-- Long: ~1000 chars each from start, middle, end
       |      |     +-- Joined with [...] markers between segments
       |      |     +-- Replaces old transcript[:3000] blind truncation
       |      |
       |      +-- prompts.score_summary() → typed ScoreRating:
       |      |     +-- completeness (1-5), coherence (1-5), faithfulness (1-5)
       |      |     +-- BAML handles JSON parsing — no manual _parse_llm_rating()
       |      |
       |      +-- llm_score = (completeness + coherence + faithfulness) / 15
       |      +-- faithfulness_score = faithfulness / 5 (tracked separately!)
       |      +-- On LLM failure: fall back to heuristic-only
       |      +-- faithfulness ≤ 0.4 → log.warning + CLI [!LOW FAITHFULNESS]
       |
       +-- quality_score = 0.4 * heuristic + 0.6 * llm --> 0.0-1.0
       |
       +-- verify=True? (opt-in via --verify flag)
       |      |
       |      +-- CLAIM VERIFICATION (1 extra LLM call via BAML VerifyClaims)
       |      |     +-- _sample_transcript(transcript, max_excerpt=6000)
       |      |     +-- prompts.verify_claims() → ClaimVerification[]
       |      |     +-- Each claim: {claim: str, verified: bool}
       |      |     +-- verification_score = verified_count / total_claims
       |      |
       |      +-- On LLM failure: verification_score = None (non-fatal)
       |
       +-- Store: UPDATE summaries SET quality_score=?, heuristic_score=?,
       |          llm_score=?, faithfulness_score=?, verification_score=?
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

```mermaid
flowchart TD
    START["get_client()<br/>(cached, reused across calls)"]

    HAS_URL{"OPENAI_BASE_URL<br/>set?"}
    IS_OLLAMA{"Points to Ollama<br/>(port 11434)?"}
    OLLAMA_URL["api_key = 'ollama'<br/>use that URL"]
    CUSTOM_URL["use OPENAI_API_KEY as-is<br/>use that URL"]

    HAS_KEY{"OPENAI_API_KEY<br/>set?"}
    OPENAI["base_url = api.openai.com/v1<br/>model default: gpt-4o-mini"]
    LOCAL["base_url = localhost:11434/v1<br/>api_key = 'ollama'<br/>model default: mistral"]

    MODEL["Model: OPENAI_MODEL env<br/>> model param > default"]

    START --> HAS_URL
    HAS_URL -- "YES" --> IS_OLLAMA
    IS_OLLAMA -- "YES" --> OLLAMA_URL --> MODEL
    IS_OLLAMA -- "NO" --> CUSTOM_URL --> MODEL
    HAS_URL -- "NO" --> HAS_KEY
    HAS_KEY -- "YES" --> OPENAI --> MODEL
    HAS_KEY -- "NO" --> LOCAL --> MODEL
```

Client cache invalidated when env vars change between calls. Retry with exponential backoff on transient LLM failures.

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

## 15. BAML Prompt Architecture

```
  Source of Truth (git-versioned):

  baml_src/
       |
       +-- clients.baml         Ollama + OpenAI client configs
       |                        Exponential retry policy
       |
       +-- summarize.baml       4 prompt functions:
       |     +-- SummarizeSinglePass(transcript, artist, video_title) -> string
       |     +-- SummarizeChunk(chunk, chunk_index, total_chunks) -> string
       |     +-- ReduceChunkSummaries(section_summaries) -> string
       |     +-- RefineSummary(prev_summary, chunk, chunk_index, total_chunks) -> string
       |     +-- All include anti-hallucination: "Do not invent names/facts"
       |
       +-- score.baml           2 prompt functions with typed outputs:
       |     +-- ScoreSummary(transcript_excerpt, summary) -> ScoreRating
       |     |     ScoreRating { completeness: int, coherence: int, faithfulness: int }
       |     +-- VerifyClaims(summary, transcript_excerpt) -> ClaimVerification[]
       |           ClaimVerification { claim: string, verified: bool }
       |
       +-- generators.baml      Code generation config (Python/Pydantic)

  Build step:                   Development workflow:

  baml-cli generate             1. Edit .baml files
       |                        2. Run baml-cli generate
       v                        3. baml_client/ regenerated
  baml_client/                  4. prompts.py picks up changes
  (auto-generated,              5. No Python source editing needed
   .gitignored)                    for prompt-only changes
       |
       v
  prompts.py (thin adapter)     Codebase-facing API:
       |
       +-- summarize_single_pass()    --> summarizer.py
       +-- summarize_chunk()          --> summarizer.py
       +-- reduce_chunk_summaries()   --> summarizer.py
       +-- refine_summary()           --> summarizer.py
       +-- score_summary()            --> scorer.py
       +-- verify_claims()            --> scorer.py
       +-- Re-exports: ScoreRating, ClaimVerification types
```

## 16. Hallucination Guardrails Stack

```
  3-tier defense against hallucinated names, facts, and claims.
  Motivated by: Hubermanlab summary hallucinated "Elijah Wood" as speaker.

  +=========================================================================+
  | TIER 1: Prompt Hardening (always on, 0 extra LLM calls)                |
  |-------------------------------------------------------------------------|
  | Every .baml prompt includes:                                            |
  |   "Only state facts, names, quotes that appear in the transcript."      |
  |   "Do not invent or assume any information."                            |
  | Single source of truth: baml_src/*.baml (git diff shows changes)        |
  +=========================================================================+
       |
       v
  +=========================================================================+
  | TIER 2: Scoring Guardrails (always on, 0 extra LLM calls)              |
  |-------------------------------------------------------------------------|
  |                                                                         |
  | A. Named Entity Verification  (_named_entity_score)                     |
  |    - Regex-extract proper nouns from summary                            |
  |    - Multi-word: "Elijah Wood", "Stanford University"                   |
  |    - Single mid-sentence: "...discussed Huberman..."                    |
  |    - Filter stopwords (months, days, "The", "However")                  |
  |    - Verify each against transcript (case-insensitive)                  |
  |    - Score = verified / total (1.0 if no entities)                      |
  |    - Weight: 0.20 of heuristic score                                    |
  |                                                                         |
  | B. Stratified Transcript Sampling  (_sample_transcript)                 |
  |    - Replaces blind transcript[:3000] with start+middle+end             |
  |    - ~1000 chars each segment, joined with [...] markers                |
  |    - Used by LLM self-check AND claim verification                      |
  |    - LLM sees representative sample from entire transcript              |
  |                                                                         |
  | C. Faithfulness Tracking                                                |
  |    - LLM ScoreRating.faithfulness extracted as separate DB column       |
  |    - Not averaged away into llm_score anymore                           |
  |    - faithfulness ≤ 0.4 → log.warning + CLI [!LOW FAITHFULNESS]         |
  |                                                                         |
  +=========================================================================+
       |
       v
  +=========================================================================+
  | TIER 3: Claim Verification (opt-in --verify, 1 extra LLM call)         |
  |-------------------------------------------------------------------------|
  | VerifyClaims BAML function:                                             |
  |   - Extracts 5 factual claims from summary                             |
  |   - Cross-references each against transcript excerpt                    |
  |   - Returns ClaimVerification[] (typed: claim + verified bool)          |
  |   - verification_score = verified_count / total_claims                  |
  |   - Stored in summaries.verification_score column                       |
  |   - CLI output: "verified=80%"                                          |
  +=========================================================================+

  Cost summary:
  +--------+------------------+----------------------------+
  | Tier   | Extra LLM Calls  | When                       |
  +--------+------------------+----------------------------+
  |   1    |        0         | Always (prompt text only)  |
  |   2    |        0         | Always (heuristic + reuse) |
  |   3    |        1         | Only with --verify flag    |
  +--------+------------------+----------------------------+
```
