"""Background job management: launch, track, attach, stop."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from yt_artist.storage import Storage

log = logging.getLogger("yt_artist.jobs")

# Minimum video count before suggesting --bg
BG_SUGGESTION_THRESHOLD = 5

# Time estimates (seconds per video, conservative)
EST_TRANSCRIBE_PER_VIDEO = 8.0   # yt-dlp subtitle fetch + processing
EST_SUMMARIZE_PER_VIDEO = 15.0   # LLM call + DB write
EST_INTER_VIDEO_DELAY = 2.0      # Default inter-video delay


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_job_id() -> str:
    """Generate a 12-hex-char unique job ID."""
    return uuid.uuid4().hex[:12]


def jobs_dir(data_dir: Path) -> Path:
    """Return the jobs log directory, creating it if needed."""
    d = data_dir / "data" / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running (POSIX)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # signal 0 = check existence only
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but we can't signal it


# ---------------------------------------------------------------------------
# Time estimation
# ---------------------------------------------------------------------------

def estimate_time(n_videos: int, operation: str, concurrency: int = 1) -> float:
    """Estimate total wall-clock seconds for a bulk operation."""
    if operation == "transcribe":
        per_video = EST_TRANSCRIBE_PER_VIDEO
    elif operation == "summarize":
        per_video = EST_SUMMARIZE_PER_VIDEO
    else:
        per_video = EST_TRANSCRIBE_PER_VIDEO + EST_SUMMARIZE_PER_VIDEO

    total_per_video = per_video + EST_INTER_VIDEO_DELAY
    effective = (n_videos * total_per_video) / max(concurrency, 1)
    return effective


def format_estimate(seconds: float) -> str:
    """Human-readable time estimate."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.0f}m"
    hours = minutes / 60
    return f"{hours:.1f}h"


# ---------------------------------------------------------------------------
# Background hint
# ---------------------------------------------------------------------------

def maybe_suggest_background(
    n_videos: int,
    operation: str,
    concurrency: int,
    original_argv: List[str],
    quiet: bool = False,
) -> None:
    """Print a hint to stderr suggesting --bg if the operation looks long.

    Does nothing when n_videos < BG_SUGGESTION_THRESHOLD or when quiet is set.
    """
    if quiet or n_videos < BG_SUGGESTION_THRESHOLD:
        return
    est = estimate_time(n_videos, operation, concurrency)
    est_str = format_estimate(est)
    # Reconstruct the command with --bg inserted after 'yt-artist'
    cmd_parts = [original_argv[0], "--bg"] + original_argv[1:]
    sys.stderr.write("\n")
    sys.stderr.write(f"  \U0001f551 This will process {n_videos} videos (estimated ~{est_str}).\n")
    sys.stderr.write(f"  To run in background, re-run with --bg:\n")
    sys.stderr.write(f"    {' '.join(cmd_parts)}\n")
    sys.stderr.write("\n")


# ---------------------------------------------------------------------------
# Job DB helpers
# ---------------------------------------------------------------------------

def _create_job_record(storage: Storage, job_id: str, command: str, log_path: Path) -> None:
    """Insert a new job row with status='running'."""
    conn = storage._conn()
    try:
        conn.execute(
            "INSERT INTO jobs (id, command, status, pid, log_file) VALUES (?, ?, 'running', -1, ?)",
            (job_id, command, str(log_path)),
        )
        conn.commit()
    finally:
        conn.close()


def _update_job_pid(storage: Storage, job_id: str, pid: int) -> None:
    """Set the actual PID after subprocess launch."""
    conn = storage._conn()
    try:
        conn.execute("UPDATE jobs SET pid = ? WHERE id = ?", (pid, job_id))
        conn.commit()
    finally:
        conn.close()


def get_job(storage: Storage, job_id: str) -> Optional[Dict[str, Any]]:
    """Get a job by ID (supports prefix match for short IDs)."""
    conn = storage._conn()
    try:
        # Exact match first
        cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        if row:
            return row
        # Prefix match
        cur = conn.execute(
            "SELECT * FROM jobs WHERE id LIKE ? ORDER BY started_at DESC LIMIT 1",
            (job_id + "%",),
        )
        return cur.fetchone()
    finally:
        conn.close()


def update_job_progress(
    storage: Storage, job_id: str,
    *, done: int = None, errors: int = None, total: int = None,
) -> None:
    """Update progress fields on a job row."""
    parts: list[str] = []
    params: list[Any] = []
    if total is not None:
        parts.append("total = ?")
        params.append(total)
    if done is not None:
        parts.append("done = ?")
        params.append(done)
    if errors is not None:
        parts.append("errors = ?")
        params.append(errors)
    if not parts:
        return
    params.append(job_id)
    conn = storage._conn()
    try:
        conn.execute(f"UPDATE jobs SET {', '.join(parts)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def finalize_job(
    storage: Storage, job_id: str,
    status: str = "completed", error_message: str = None,
) -> None:
    """Mark a job as finished (completed, failed, or stopped)."""
    conn = storage._conn()
    try:
        conn.execute(
            "UPDATE jobs SET status = ?, finished_at = datetime('now'), error_message = ? "
            "WHERE id = ?",
            (status, error_message, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_job_stale(storage: Storage, job_id: str) -> None:
    """Mark a running job whose process died as failed."""
    conn = storage._conn()
    try:
        conn.execute(
            "UPDATE jobs SET status = 'failed', finished_at = datetime('now'), "
            "error_message = 'Process died unexpectedly' WHERE id = ? AND status = 'running'",
            (job_id,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Launch background job
# ---------------------------------------------------------------------------

def launch_background(
    argv: List[str],
    storage: Storage,
    data_dir: Path,
) -> str:
    """Re-launch the current command as a detached background process.

    Strips --bg/--background from argv, adds --_bg-worker <job_id>.
    Returns the job_id.
    """
    job_id = _generate_job_id()
    log_dir = jobs_dir(data_dir)
    log_path = log_dir / f"{job_id}.log"

    # Build child argv: python -m yt_artist.cli <original args minus --bg>
    child_argv = [sys.executable, "-m", "yt_artist.cli"]
    for arg in argv[1:]:  # skip original argv[0]
        if arg in ("--bg", "--background"):
            continue
        child_argv.append(arg)
    child_argv.extend(["--_bg-worker", job_id])

    # Human-readable command for display
    display_cmd = " ".join(a for a in argv[1:] if a not in ("--bg", "--background"))

    # Register job in DB before launch
    _create_job_record(storage, job_id, display_cmd, log_path)

    # Launch detached subprocess
    log_fh = open(log_path, "w")  # noqa: SIM115
    proc = subprocess.Popen(
        child_argv,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )

    _update_job_pid(storage, job_id, proc.pid)
    return job_id


# ---------------------------------------------------------------------------
# List / attach / stop / clean
# ---------------------------------------------------------------------------

def list_jobs(storage: Storage, status_filter: str = None) -> List[Dict[str, Any]]:
    """Return recent jobs, optionally filtered by status.  Auto-detects stale PIDs."""
    conn = storage._conn()
    try:
        if status_filter:
            cur = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY started_at DESC LIMIT 20",
                (status_filter,),
            )
        else:
            cur = conn.execute("SELECT * FROM jobs ORDER BY started_at DESC LIMIT 20")
        rows = cur.fetchall()
    finally:
        conn.close()

    # Clean up stale jobs (pid no longer alive but status is 'running')
    for row in rows:
        if row["status"] == "running" and not _is_pid_alive(row["pid"]):
            _mark_job_stale(storage, row["id"])
            row["status"] = "failed"
            row["error_message"] = "Process died unexpectedly"

    return rows


def attach_job(storage: Storage, job_id: str) -> None:
    """Tail the log file for a job.  Ctrl-C to detach (job keeps running)."""
    job = get_job(storage, job_id)
    if not job:
        raise SystemExit(f"Job {job_id} not found.")

    log_path = Path(job["log_file"])
    if not log_path.exists():
        raise SystemExit(f"Log file not found: {log_path}")

    sys.stderr.write(f"Attached to job {job['id'][:8]} (PID {job['pid']}). Press Ctrl-C to detach.\n")
    sys.stderr.write(f"Command: {job['command']}\n")
    sys.stderr.write("---\n")

    try:
        with open(log_path, "r") as f:
            # Print existing content
            for line in f:
                sys.stdout.write(line)

            # Tail for new content while job is running
            while True:
                line = f.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    # Check if job is still running
                    current = get_job(storage, job["id"])
                    if current and current["status"] != "running":
                        remaining = f.read()
                        if remaining:
                            sys.stdout.write(remaining)
                        sys.stderr.write(f"\n--- Job {job['id'][:8]} finished (status: {current['status']})\n")
                        break
                    time.sleep(0.5)
    except KeyboardInterrupt:
        sys.stderr.write(f"\n--- Detached from job {job['id'][:8]} (still running in background)\n")


def stop_job(storage: Storage, job_id: str) -> None:
    """Send SIGTERM to a running background job."""
    job = get_job(storage, job_id)
    if not job:
        raise SystemExit(f"Job {job_id} not found.")
    if job["status"] != "running":
        raise SystemExit(f"Job {job['id'][:8]} is not running (status: {job['status']}).")

    pid = job["pid"]
    if not _is_pid_alive(pid):
        _mark_job_stale(storage, job["id"])
        raise SystemExit(f"Job {job['id'][:8]} process (PID {pid}) is not alive. Marked as failed.")

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to job {job['id'][:8]} (PID {pid}).")
    except ProcessLookupError:
        _mark_job_stale(storage, job["id"])
        print(f"Process already dead. Job marked as failed.")
        return
    except PermissionError:
        raise SystemExit(f"Cannot signal PID {pid} (permission denied).")

    finalize_job(storage, job["id"], status="stopped")


def cleanup_old_jobs(storage: Storage, max_age_days: int = 7) -> int:
    """Remove finished jobs (+ log files) older than max_age_days.  Returns count removed."""
    conn = storage._conn()
    try:
        cur = conn.execute(
            "SELECT id, log_file FROM jobs WHERE status != 'running' "
            "AND finished_at < datetime('now', ?)",
            (f"-{max_age_days} days",),
        )
        rows = cur.fetchall()
        for row in rows:
            log_path = Path(row["log_file"])
            if log_path.exists():
                log_path.unlink()
        if rows:
            conn.execute(
                "DELETE FROM jobs WHERE status != 'running' "
                "AND finished_at < datetime('now', ?)",
                (f"-{max_age_days} days",),
            )
            conn.commit()
        return len(rows)
    finally:
        conn.close()
