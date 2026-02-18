"""Centralized path construction for all runtime data files.

Pure functions — no side effects (no mkdir). Callers are responsible for
creating directories before writing.
"""

from __future__ import annotations

import re
from pathlib import Path


def db_path(data_dir: Path) -> Path:
    """Return the default database file path."""
    return data_dir / "data" / "yt_artist.db"


def urllist_rel_path(artist_id: str, artist_name: str) -> str:
    """Return relative urllist path matching the DB-stored format.

    Example: ``data/artists/UC_xyz/artistUC_xyzMy_Channel-urllist.md``
    """
    safe_name = re.sub(r"[^\w\-]", "_", artist_name).strip("_") or "channel"
    return f"data/artists/{artist_id}/artist{artist_id}{safe_name}-urllist.md"


def urllist_abs_path(data_dir: Path, artist_id: str, artist_name: str) -> Path:
    """Return absolute urllist path."""
    return data_dir / urllist_rel_path(artist_id, artist_name)


def transcript_dir(data_dir: Path, artist_id: str) -> Path:
    """Return directory for storing transcript text files."""
    return data_dir / "artists" / artist_id / "transcripts"


def transcript_file(data_dir: Path, artist_id: str, video_id: str) -> Path:
    """Return path for a single transcript text file."""
    return transcript_dir(data_dir, artist_id) / f"{video_id}.txt"


def jobs_dir(data_dir: Path) -> Path:
    """Return directory for job log files."""
    return data_dir / "data" / "jobs"


def job_log_file(data_dir: Path, job_id: str) -> Path:
    """Return path for a specific job log file."""
    return jobs_dir(data_dir) / f"{job_id}.log"
