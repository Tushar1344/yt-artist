# ADR-0008: Background jobs for long-running bulk operations

## Status

Accepted (2026-02-09).

## Context

Bulk transcribe/summarize of a full channel (50–500+ videos) can block the terminal for minutes to hours. Users need a way to push long-running operations to the background, monitor progress, and continue using yt-artist for other tasks without waiting.

Key requirements:
- **No new dependencies** — stdlib only (no Celery, no Redis, no external task queue).
- **Progress visibility** — users can check how far along a job is at any time.
- **Crash safety** — if a background process dies, the system should detect it rather than leaving ghost "running" entries.
- **Simple UX** — one flag (`--bg`) to push to background, one command (`jobs`) to manage.

## Decision

### Process model: OS-level detachment

Use `subprocess.Popen(start_new_session=True)` to re-execute the current command as a detached child process. The parent process:
1. Strips `--bg` from argv
2. Adds hidden `--_bg-worker <job_id>` flag
3. Creates a job record in SQLite
4. Launches the child with stdout/stderr redirected to a log file
5. Prints the job ID and exits immediately

The child process runs the actual bulk operation, updating progress in the SQLite `jobs` table.

### Progress tracking: dual-write to log + SQLite

- **Log file** (`<data_dir>/data/jobs/<job_id>.log`): Human-readable, tail-able. Captures all stdout/stderr from the child.
- **SQLite `jobs` table**: Machine-queryable progress fields (total, done, errors, status). Updated on each video processed via the existing `_ProgressCounter`.

### Stale PID detection

`list_jobs()` checks every "running" job with `os.kill(pid, 0)`. If the process is dead, the job is auto-marked as `failed` with message "Process died unexpectedly". This handles OOM kills, segfaults, and any crash the child didn't catch.

### Signal handling

Background worker processes register a SIGTERM handler. When `jobs stop <id>` sends SIGTERM, the handler updates the job status to "stopped" and exits cleanly.

### Time estimation + suggestion

Before bulk operations with ≥5 videos, print a time estimate and suggest `--bg`:
```
  🕑 This will process 50 videos (estimated ~7m).
  To run in background, re-run with --bg:
    yt-artist --bg transcribe --artist-id @TED
```

### Schema

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
```

## Alternatives Considered

| Alternative | Why rejected |
|-------------|-------------|
| Celery / Redis task queue | Massive new dependency; overkill for a CLI tool |
| Python threading with daemon | Can't survive terminal close; same PID means same process group |
| `nohup` wrapper script | Non-portable; no progress tracking; hard to integrate with SQLite |
| `multiprocessing.Process` | Shares address space complications; `fork()` + SQLite = risky |

## Consequences

- Users can push any bulk operation to background with `--bg`.
- `yt-artist jobs` provides list, attach, stop, and clean subcommands.
- SQLite WAL mode (already enabled) allows concurrent reads from foreground while background writes.
- No new pip dependencies added.
- 32 new tests cover the entire background jobs system.
- `_ProgressCounter` remains backward-compatible: when `job_id` is None (foreground mode), no DB writes occur.
