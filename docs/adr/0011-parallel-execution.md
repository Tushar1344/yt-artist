# ADR-0011: Parallel execution for bulk operations

## Status

Accepted (2026-02-09).

## Context

Processing 100+ videos sequentially is slow. Transcription involves network I/O (downloading subtitles from YouTube) and summarization involves LLM API calls — both are I/O-bound and benefit from parallelism.

## Decision

### ThreadPoolExecutor for bulk operations

Both `_cmd_transcribe` (bulk) and `_cmd_summarize` (bulk) use `concurrent.futures.ThreadPoolExecutor` with configurable `max_workers`.

- Workers default to `get_max_concurrency()` (default: 2, configurable via `YT_ARTIST_MAX_CONCURRENCY`).
- Each worker processes one video at a time: download subtitle → save to DB (transcribe) or read transcript → call LLM → save summary (summarize).
- Inter-video delay is applied per-worker between videos.

### Thread-safe progress tracking

`_ProgressCounter` uses a threading lock to ensure atomic updates to `done`, `errors`, and `total` counters. When background job mode is active, DB writes are also serialized through the lock.

### Batch DB queries

- Before bulk transcribe: batch-fetch all existing transcripts for the artist to skip already-transcribed videos.
- Before bulk summarize: batch-fetch all existing summaries to skip already-summarized videos.
- This avoids N individual DB queries for N videos.

### Speculative subtitle download

In parallel transcription, yt-dlp is called with both `--write-auto-subs` and `--write-subs` flags to try auto-generated captions first, falling back to manual captions. This reduces the number of yt-dlp invocations per video.

## Alternatives Considered

| Alternative | Why not chosen |
|-------------|---------------|
| `multiprocessing` | SQLite + fork() is fragile; threads are simpler for I/O-bound work |
| `asyncio` | Would require rewriting all I/O to be async; yt-dlp is synchronous |
| Higher default concurrency | Risk of YouTube rate limiting; 2 is conservative and safe |

## Consequences

- Bulk operations are ~2x faster with default concurrency of 2.
- Users can increase concurrency for faster processing (at their own risk for rate limits).
- Thread safety is maintained through locking in `_ProgressCounter`.
- 16 new tests cover parallel execution, progress tracking, and batch queries.
