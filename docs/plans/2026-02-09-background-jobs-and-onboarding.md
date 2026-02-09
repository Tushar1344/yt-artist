# Background jobs, parallel execution, rate-limit safety, and guided onboarding

**Date:** 2026-02-09

## Summary

Four major feature areas implemented across sessions 3-5 of the collaborative development:

1. **Parallel execution** (session 3): Bulk transcribe and summarize use `ThreadPoolExecutor` with configurable concurrency. Batch DB queries skip already-processed videos. 109 → tests.

2. **Rate-limit safety** (session 4): Conservative defaults for yt-dlp sleep flags, inter-video delay, max concurrency. Optimistic subtitle download. Cookie support for restricted content. Exponential backoff on 429. 117 tests.

3. **Guided onboarding** (session 4): `--quiet`/`-q` flag, next-step hints after every command, `quickstart` subcommand with @TED walkthrough, first-run detection. All hints go to stderr. 138 tests.

4. **Background jobs** (session 5): `--bg` flag for background execution, `jobs` subcommand (list/attach/stop/clean), SQLite `jobs` table, time estimation, dual-write progress, SIGTERM handler, stale PID detection. 170 tests.

## Implementation order

### Parallel execution
1. `ThreadPoolExecutor` in `_cmd_transcribe` and `_cmd_summarize` bulk paths
2. Thread-safe `_ProgressCounter` with locking
3. Batch DB queries for skip-already-processed optimization
4. Speculative subtitle download (auto-subs first, manual fallback)

### Rate-limit safety
1. `yt_dlp_util.py`: sleep flags, max concurrency, inter-video delay helpers
2. `transcriber.py`: optimistic subtitle download with fallback
3. `cli.py`: inter-video delay between bulk operations
4. Environment variable configuration for all parameters

### Guided onboarding
1. `--quiet`/`-q` global flag on argparse
2. `_hint()` helper writing to stderr
3. Per-command hint logic after each `args.func()` call
4. `quickstart` subcommand with @TED example
5. First-run detection (empty artists table)
6. Tests for all hint content and suppression

### Background jobs
1. Schema: `jobs` table in `schema.sql` + migration in `storage.py`
2. `jobs.py` module: launch, list, attach, stop, cleanup, time estimation
3. `_ProgressCounter` extended for optional DB dual-write
4. CLI: `--bg` flag, `--_bg-worker` hidden flag, `jobs` subcommand
5. Background dispatch in `main()`: parent launches child, child sets globals
6. SIGTERM handler, crash safety (try/except wrapping)
7. Background suggestion hint before bulk operations (>=5 videos)

## Key files changed

| File | Changes |
|------|---------|
| `src/yt_artist/schema.sql` | Added `jobs` table |
| `src/yt_artist/storage.py` | Added `_migrate_jobs_table()` |
| `src/yt_artist/jobs.py` | **New**: 381 lines, all job management |
| `src/yt_artist/cli.py` | `--bg`, `--_bg-worker`, `jobs` subcommand, `_ProgressCounter` dual-write, hints, quickstart, first-run detection |
| `src/yt_artist/yt_dlp_util.py` | Sleep flags, concurrency, inter-video delay helpers |
| `src/yt_artist/transcriber.py` | Optimistic subtitle download |
| `tests/test_background_jobs.py` | **New**: 439 lines, 32 tests |
| `tests/test_onboarding.py` | **New**: 359 lines, 22 tests |
| `tests/test_parallel.py` | **New**: 265 lines, 16 tests |
| `tests/test_ux_improvements.py` | Extended: 249 lines |
| `tests/test_yt_dlp_util.py` | Extended: 119 lines |

## References

- [ADR 0008](../adr/0008-background-jobs.md) — Background jobs
- [ADR 0009](../adr/0009-guided-onboarding.md) — Guided onboarding
- [ADR 0010](../adr/0010-rate-limit-safety.md) — Rate-limit safety
- [ADR 0011](../adr/0011-parallel-execution.md) — Parallel execution
