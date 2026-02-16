# ADR-0012: Pipeline parallelism for bulk transcribe + summarize

## Status

Accepted (2026-02-16). Implemented in Phase 4.

## Context

Today, `summarize --artist-id @X` follows a strict two-phase execution model:

1. **Phase 1 — Transcribe all missing:** Walk the full video list, transcribe every video that lacks a transcript, wait for all to finish.
2. **Phase 2 — Summarize all:** Walk the full video list again, summarize every video that has a transcript but no summary.

This is simple but wasteful. During a 459-video bulk run on @hubermanlab, we observed:

- Phase 1 took ~10+ hours (YouTube rate limiting after ~300 videos).
- Phase 2 (local Ollama) was blocked the entire time, even though 300+ transcripts were already available to summarize.
- Two concurrent bulk commands (one transcribe, one summarize) fight over YouTube with duplicate requests and compounding 429 errors.

The underlying issue: **transcribe is I/O-bound on YouTube; summarize is I/O-bound on the LLM.** These are independent bottlenecks that can run concurrently.

## Decision

### Proposed: pipeline parallelism in bulk summarize

When `summarize --artist-id @X` detects missing transcripts, instead of transcribing all-then-summarizing-all, it should run a **producer-consumer pipeline**:

- **Producer (transcribe worker):** Picks up videos without transcripts, downloads subtitles from YouTube, writes transcript to DB. Feeds completed video IDs into a queue.
- **Consumer (summarize worker):** Picks up videos with transcripts but no summaries (including those just produced), calls the LLM, writes summary to DB.
- Both run concurrently using the existing `ThreadPoolExecutor`.

```
[YouTube] --transcribe--> [DB: transcript] --summarize--> [DB: summary]
     ^                         ^                              ^
  I/O-bound (network)    shared state (SQLite)        I/O-bound (LLM)
```

### Key design points

1. **Shared queue or DB polling:** Two options:
   - **Queue-based:** Transcribe worker pushes video IDs to a `queue.Queue`; summarize worker reads from it. Lower latency, tighter coupling.
   - **DB-polling:** Summarize worker periodically queries for "transcribed but not summarized" videos. Simpler, naturally idempotent, works if the user also runs a separate transcribe command.
   - **Recommendation:** DB-polling. It's simpler, reuses existing batch-query logic, and handles crash recovery (no in-memory queue to lose).

2. **Concurrency budget:** The existing `MAX_CONCURRENCY` (default: 2) should be split between transcribe and summarize workers. For example, with concurrency=2: 1 transcribe worker + 1 summarize worker. With concurrency=4: 2+2 or 1+3 (summarize is faster, so it can use fewer workers).

3. **Termination:** The summarize polling loop stops when: (a) all videos have summaries, OR (b) the transcribe producer signals it's done and the summarize queue is drained.

4. **No change to standalone commands:** `transcribe --artist-id @X` and `summarize --artist-id @X` (when all transcripts exist) continue to work exactly as today. Pipeline mode only activates when summarize discovers missing transcripts.

### Performance estimate

From the @hubermanlab run:
- Transcribe rate (steady): ~4 videos/min
- Summarize rate (local Ollama/mistral): ~5 videos/min (12s per video including truncation)
- With pipeline: summarize starts after the first transcript lands (~15s), not after all 459 transcripts are done (~10 hours). Time to first summary drops from hours to seconds.

## Alternatives Considered

| Alternative | Why not chosen |
|-------------|---------------|
| User runs two commands in parallel | Causes duplicate YouTube requests and 429 rate-limit escalation (observed in @hubermanlab run) |
| Async/await pipeline | Requires rewriting yt-dlp calls and LLM client to async; high effort for marginal benefit |
| Batch chunking (transcribe 50, summarize 50, repeat) | Better than today but still has idle time; pipeline is strictly better |

## Consequences

- First summary arrives in seconds instead of hours for large channels.
- YouTube rate-limit pressure is reduced (no duplicate requests from competing processes).
- Summarize command becomes slightly more complex (polling loop + termination logic).
- Existing tests for sequential transcribe and summarize remain valid; new tests needed for pipeline mode.
- The `--bg` background job mode works unchanged (the pipeline runs inside the single background process).
