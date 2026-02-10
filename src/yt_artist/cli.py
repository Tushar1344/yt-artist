"""CLI entrypoint: fetch-channel, transcribe, summarize. Dependencies (urllist, transcripts) are auto-created and reported."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Ensure output appears in terminal (e.g. when run as pip-installed console_scripts)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

from yt_artist import __version__
from yt_artist.artist_prompt import build_artist_about
from yt_artist.fetcher import ensure_artist_and_video_for_video_url, fetch_channel
from yt_artist.llm import check_connectivity as _check_llm
from yt_artist.storage import Storage
from yt_artist.summarizer import summarize
from yt_artist.transcriber import transcribe, extract_video_id
from yt_artist.yt_dlp_util import channel_url_for, MAX_CONCURRENCY, get_inter_video_delay, get_auth_config

log = logging.getLogger("yt_artist.cli")

# Module-level quiet flag — set once in main(), read-only afterward.
_quiet = False

# Background worker state — set in main() when --_bg-worker is active.
_bg_job_id: str | None = None
_bg_storage: Storage | None = None

# Demo channel for quickstart walkthrough.
_DEMO_CHANNEL = "@TED"
_DEMO_CHANNEL_URL = "https://www.youtube.com/@TED"
_DEMO_VIDEO_URL = "https://www.youtube.com/watch?v=UyyjU8fzEYU"
_DEMO_VIDEO_ID = "UyyjU8fzEYU"


def _hint(*lines: str) -> None:
    """Print next-step hints to stderr.  Suppressed when --quiet is active."""
    if _quiet:
        return
    sys.stderr.write("\n")
    for line in lines:
        sys.stderr.write(f"  {line}\n")


def _report_dependency(message: str) -> None:
    """Log one short line when auto-creating dependencies."""
    log.info("Dependencies: %s", message)


class _ProgressCounter:
    """Thread-safe counter for parallel bulk operations.

    When *job_id* and *job_storage* are provided (background-worker mode),
    each tick() also persists done/errors to the ``jobs`` table so that
    ``yt-artist jobs`` can display live progress from another process.
    """

    def __init__(
        self,
        total: int,
        *,
        job_id: str | None = None,
        job_storage: Storage | None = None,
    ) -> None:
        self.total = total
        self._done = 0
        self._errors = 0
        self._lock = threading.Lock()
        self.t0 = time.monotonic()
        self._job_id = job_id
        self._job_storage = job_storage
        if job_id and job_storage:
            from yt_artist.jobs import update_job_progress
            update_job_progress(job_storage, job_id, total=total)

    def tick(self, label: str, video_id: str, error: str | None = None) -> None:
        with self._lock:
            self._done += 1
            if error:
                self._errors += 1
            n, errs = self._done, self._errors
        elapsed = time.monotonic() - self.t0
        eta = ""
        if n > 0 and n < self.total:
            avg = elapsed / n
            remaining = avg * (self.total - n)
            eta = f"  ETA {remaining:.0f}s"
        status = f"  ERROR: {error}" if error else ""
        log.info("%s %d/%d: %s  [%.0fs elapsed%s]%s", label, n, self.total, video_id, elapsed, eta, status)
        # Persist to jobs table for background mode
        if self._job_id and self._job_storage:
            from yt_artist.jobs import update_job_progress
            update_job_progress(self._job_storage, self._job_id, done=n, errors=errs)

    def finalize(self, status: str = "completed", error_message: str | None = None) -> None:
        """Mark the job as finished in the DB (no-op in foreground mode)."""
        if not self._job_id or not self._job_storage:
            return
        from yt_artist.jobs import finalize_job
        finalize_job(self._job_storage, self._job_id, status=status, error_message=error_message)

    @property
    def errors(self) -> int:
        with self._lock:
            return self._errors

    @property
    def done(self) -> int:
        with self._lock:
            return self._done


def _resolve_prompt_id(storage: Storage, artist_id: str | None, prompt_override: str | None) -> str:
    """Resolve prompt: --prompt else artist default else YT_ARTIST_DEFAULT_PROMPT else first prompt in DB."""
    if prompt_override and prompt_override.strip():
        pid = prompt_override.strip()
        if storage.get_prompt(pid):
            return pid
        raise SystemExit(f"Prompt '{pid}' not found. Run yt-artist list-prompts to see available prompts.")
    if artist_id:
        pid = storage.get_artist_default_prompt_id(artist_id)
        if pid:
            return pid
    env_default = (os.environ.get("YT_ARTIST_DEFAULT_PROMPT") or "").strip()
    if env_default and storage.get_prompt(env_default):
        return env_default
    rows = storage.list_prompts()
    if rows:
        return rows[0]["id"]
    raise SystemExit("Set a default prompt for this artist (yt-artist set-default-prompt) or pass --prompt.")


def _default_data_dir() -> Path:
    return Path.cwd()


def _default_db_path(data_dir: Path) -> Path:
    return data_dir / "data" / "yt_artist.db"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yt-artist",
        description="Fetch YouTube channel URLs, transcribe videos, generate AI summaries.",
        epilog=(
            "Rate-limit safety: yt-dlp calls include --sleep-requests and "
            "--sleep-subtitles pauses.  Override with env vars "
            "YT_ARTIST_SLEEP_REQUESTS / YT_ARTIST_SLEEP_SUBTITLES (seconds).  "
            "Set YT_ARTIST_INTER_VIDEO_DELAY to control pause between videos "
            "(default 2s).\n\n"
            "Cookie warning: YT_ARTIST_COOKIES_BROWSER / YT_ARTIST_COOKIES_FILE "
            "tie automated activity to your Google account.  YouTube can suspend "
            "accounts used with automated tools.  NEVER use your primary Google "
            "account — use a throwaway / secondary account."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to SQLite DB (default: <data-dir>/data/yt_artist.db)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Data directory for urllists and optional transcript files (default: cwd)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        metavar="N",
        help=(
            f"Number of parallel workers for bulk transcribe/summarize "
            f"(default: 1, max: {MAX_CONCURRENCY}). Higher values increase "
            f"YouTube rate-limit risk."
        ),
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        default=False,
        help="Suppress next-step hints after commands (for scripting/piping)",
    )
    parser.add_argument(
        "--bg", "--background",
        action="store_true",
        default=False,
        dest="background",
        help="Run bulk operations in the background. Use 'yt-artist jobs' to check progress.",
    )
    parser.add_argument(
        "--_bg-worker",
        default=None,
        dest="bg_worker_job_id",
        help=argparse.SUPPRESS,  # Internal: marks this process as a background worker
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # fetch-channel / urllist <channel_url>
    p_fetch = subparsers.add_parser("fetch-channel", help="Fetch all video URLs for a channel (bulk urllist per artist)")
    p_fetch.add_argument("channel_url", help="YouTube channel URL (e.g. https://www.youtube.com/@handle)")
    p_fetch.set_defaults(func=_cmd_fetch_channel)
    p_urllist = subparsers.add_parser("urllist", help="Alias for fetch-channel: fetch all video URLs for a channel")
    p_urllist.add_argument("channel_url", help="YouTube channel URL")
    p_urllist.set_defaults(func=_cmd_fetch_channel)

    # transcribe [video_url | --artist-id ARTIST_ID]
    p_trans = subparsers.add_parser("transcribe", help="Transcribe one video or all videos for an artist (bulk). Auto-fetches urllist if artist missing.")
    p_trans.add_argument("video_url", nargs="?", default=None, help="Video URL or video ID (omit if using --artist-id)")
    p_trans.add_argument("--video-id", dest="video_id", help="Video ID (alternative to positional URL)")
    p_trans.add_argument("--artist-id", default=None, help="Transcribe all videos for this artist (bulk)")
    p_trans.add_argument(
        "--write-file",
        action="store_true",
        help="Also write transcript to data/artists/<id>/transcripts/<video_id>.txt",
    )
    p_trans.set_defaults(func=_cmd_transcribe)

    # summarize [video_url_or_id | --artist-id ARTIST_ID] [--prompt ID] — one command, per-video or bulk; dependencies auto-created
    p_sum = subparsers.add_parser("summarize", help="Summarize one video or all transcribed videos for an artist. Auto-creates artist/transcript if missing.")
    p_sum.add_argument("video", nargs="?", default=None, help="Video URL or video ID (omit if using --artist-id)")
    p_sum.add_argument("--artist-id", default=None, help="Summarize all transcribed videos for this artist (bulk)")
    p_sum.add_argument("--prompt", default=None, dest="prompt_id", help="Prompt id (overrides artist default and env default)")
    p_sum.add_argument("--intent", default=None, help="Override intent for this run")
    p_sum.add_argument("--audience", default=None, help="Override audience for this run")
    p_sum.add_argument("--max-preview", type=int, default=500, help="Max chars of summary to print (0 = all)")
    p_sum.set_defaults(func=_cmd_summarize)

    # add-prompt: define a prompt template
    p_add_prompt = subparsers.add_parser("add-prompt", help="Add or update a prompt template for summaries")
    p_add_prompt.add_argument("--id", required=True, dest="prompt_id", help="Unique prompt id")
    p_add_prompt.add_argument("--name", required=True, help="Human-readable name")
    p_add_prompt.add_argument("--template", required=True, help="Template with {artist}, {video}, {intent}, {audience}")
    p_add_prompt.add_argument("--artist-component", default="", help="Hint: artist component (e.g. channel name)")
    p_add_prompt.add_argument("--video-component", default="", help="Hint: video component (e.g. title)")
    p_add_prompt.add_argument("--intent-component", default="", help="Hint: intent (e.g. key takeaways)")
    p_add_prompt.add_argument("--audience-component", default="", help="Hint: audience (e.g. social media)")
    p_add_prompt.set_defaults(func=_cmd_add_prompt)

    # list-prompts
    p_list_prompts = subparsers.add_parser("list-prompts", help="List stored prompt templates")
    p_list_prompts.set_defaults(func=_cmd_list_prompts)

    # search-transcripts: search DB for transcripts
    p_search = subparsers.add_parser("search-transcripts", help="Search/list transcripts in the DB")
    p_search.add_argument("--artist-id", default=None, help="Filter by artist (channel) id")
    p_search.add_argument("--video-id", default=None, help="Show only this video id")
    p_search.set_defaults(func=_cmd_search_transcripts)

    # set-default-prompt: per-artist default for summarize
    p_set_default = subparsers.add_parser("set-default-prompt", help="Set the default prompt for an artist (used when --prompt is not passed)")
    p_set_default.add_argument("--artist-id", required=True, help="Artist (channel) id, e.g. @NateBJones")
    p_set_default.add_argument("--prompt", required=True, dest="prompt_id", help="Prompt id to use as default")
    p_set_default.set_defaults(func=_cmd_set_default_prompt)

    # build-artist-prompt: search artist, build 'about', optionally set as default prompt
    p_build = subparsers.add_parser("build-artist-prompt", help="Search and build 'about' text for an artist; optionally save as default prompt. Optional: install duckduckgo-search for search.")
    p_build.add_argument("--artist-id", required=True, help="Artist (channel) id, e.g. @NateBJones")
    p_build.add_argument("--channel-url", default=None, help="Channel URL (to fetch artist if not in DB)")
    p_build.add_argument("--save-as-default", action="store_true", help="Create a prompt from about and set as artist default")
    p_build.set_defaults(func=_cmd_build_artist_prompt)

    # quickstart: guided tour for new users
    p_quickstart = subparsers.add_parser(
        "quickstart",
        help="Guided tour: shows the 3-step workflow with copy-pasteable example commands",
    )
    p_quickstart.set_defaults(func=_cmd_quickstart)

    # jobs: manage background jobs
    p_jobs = subparsers.add_parser("jobs", help="List and manage background jobs")
    p_jobs_sub = p_jobs.add_subparsers(dest="jobs_action")
    p_jobs.set_defaults(func=_cmd_jobs, jobs_action="list")
    p_jobs_attach = p_jobs_sub.add_parser("attach", help="Attach to a running job's output (like tail -f)")
    p_jobs_attach.add_argument("job_id", help="Job ID (or prefix)")
    p_jobs_attach.set_defaults(func=_cmd_jobs, jobs_action="attach")
    p_jobs_stop = p_jobs_sub.add_parser("stop", help="Stop a running background job")
    p_jobs_stop.add_argument("job_id", help="Job ID (or prefix)")
    p_jobs_stop.set_defaults(func=_cmd_jobs, jobs_action="stop")
    p_jobs_clean = p_jobs_sub.add_parser("clean", help="Remove finished jobs older than 7 days")
    p_jobs_clean.set_defaults(func=_cmd_jobs, jobs_action="clean")

    # doctor: pre-flight checks for setup
    p_doctor = subparsers.add_parser(
        "doctor",
        help="Check your setup: yt-dlp, YouTube auth, PO token, LLM endpoint",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    args = parser.parse_args()

    # Configure logging: default INFO; YT_ARTIST_LOG_LEVEL overrides (e.g. DEBUG, WARNING).
    level_name = os.environ.get("YT_ARTIST_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=getattr(logging, level_name, logging.INFO),
        stream=sys.stderr,
    )

    # Set module-level quiet flag from parsed args.
    global _quiet  # noqa: PLW0603
    _quiet = getattr(args, "quiet", False)

    # Clamp concurrency to [1, MAX_CONCURRENCY] to prevent YouTube rate-limit issues.
    args.concurrency = max(1, min(getattr(args, "concurrency", 1) or 1, MAX_CONCURRENCY))

    data_dir = args.data_dir or _default_data_dir()
    db_path = args.db or _default_db_path(data_dir)
    # If --db "$DB" and $DB is unset, we get empty string; use default so SQLite can open the file
    if db_path is not None and str(db_path).strip() == "":
        db_path = _default_db_path(data_dir)
    db_path = Path(db_path)
    storage = Storage(db_path)
    storage.ensure_schema()

    # --- Background job dispatch ---
    bg_worker_id = getattr(args, "bg_worker_job_id", None)

    if getattr(args, "background", False) and not bg_worker_id:
        # Parent process: launch as background and exit immediately.
        from yt_artist.jobs import launch_background
        job_id = launch_background(sys.argv, storage, data_dir)
        print(f"Job started: {job_id[:8]}")
        print(f"Check progress: yt-artist jobs")
        print(f"Attach to log:  yt-artist jobs attach {job_id[:8]}")
        print(f"Stop job:       yt-artist jobs stop {job_id[:8]}")
        return

    # If running as background worker, set module globals and register SIGTERM handler.
    if bg_worker_id:
        global _bg_job_id, _bg_storage  # noqa: PLW0603
        _bg_job_id = bg_worker_id
        _bg_storage = storage

        import signal as _signal

        def _bg_sigterm_handler(signum, frame):
            from yt_artist.jobs import finalize_job
            finalize_job(storage, bg_worker_id, status="stopped")
            sys.exit(0)

        _signal.signal(_signal.SIGTERM, _bg_sigterm_handler)

    # First-run hint: suggest doctor + quickstart if DB is empty.
    if not _quiet and args.command not in ("quickstart", "doctor", "jobs"):
        if not storage.list_artists():
            sys.stderr.write(
                "\n  \U0001f4a1 First time? Check your setup and get started:\n"
                "     yt-artist doctor      — verify yt-dlp, auth, and LLM\n"
                "     yt-artist quickstart  — guided 3-step walkthrough\n\n"
            )

    try:
        args.func(args, storage, data_dir)
        # If background worker completed normally, ensure job is marked done.
        if bg_worker_id:
            from yt_artist.jobs import get_job, finalize_job
            job = get_job(storage, bg_worker_id)
            if job and job["status"] == "running":
                finalize_job(storage, bg_worker_id, status="completed")
    except KeyboardInterrupt:
        if bg_worker_id:
            from yt_artist.jobs import finalize_job
            finalize_job(storage, bg_worker_id, status="stopped")
        log.info("Interrupted.")
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        if bg_worker_id:
            from yt_artist.jobs import finalize_job
            finalize_job(storage, bg_worker_id, status="failed", error_message=str(e)[:500])
        log.error("yt-artist error: %s", e)
        log.debug("Traceback:", exc_info=True)
        sys.exit(1)


def _cmd_fetch_channel(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """Bulk urllist for channel; writes markdown and upserts artists/videos. No dependency fill."""
    path, count = fetch_channel(args.channel_url, storage, data_dir=data_dir)
    print(f"Urllist: {path}")
    print(f"Videos:  {count}")
    # Hint: suggest next step — transcribe
    artist_id = args.channel_url.rstrip("/").split("/")[-1]
    videos = storage.list_videos(artist_id=artist_id)
    sample = videos[0]["id"] if videos else "VIDEO_ID"
    _hint(
        "\U0001f4a1 Next: transcribe a single video:",
        f'   yt-artist transcribe "https://youtube.com/watch?v={sample}"',
        "",
        f"   Or bulk-transcribe all {count} videos:",
        f"   yt-artist transcribe --artist-id {artist_id}",
    )


def _cmd_transcribe(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """Per-video or bulk by --artist-id; reports and runs fetch_channel if artist/videos missing."""
    video_url_or_id = (args.video_url or args.video_id or "").strip()
    artist_id_arg = (args.artist_id or "").strip()
    if video_url_or_id and artist_id_arg:
        raise SystemExit("Provide either video (URL or id) or --artist-id, not both.")
    if not video_url_or_id and not artist_id_arg:
        raise SystemExit("Provide video_url (or --video-id) or --artist-id.")

    if artist_id_arg:
        # Bulk: transcribe all videos for artist; fetch urllist if artist/videos missing
        artist = storage.get_artist(artist_id_arg)
        videos = storage.list_videos(artist_id=artist_id_arg) if artist else []
        if not artist or not videos:
            _report_dependency(f"artist {artist_id_arg} or videos missing → fetching urllist.")
            channel_url = channel_url_for(artist_id_arg)
            path, count = fetch_channel(channel_url, storage, data_dir=data_dir)
            _report_dependency(f"Fetched urllist for {artist_id_arg} ({count} videos).")
            videos = storage.list_videos(artist_id=artist_id_arg)
        # Batch DB check: one query instead of N individual get_transcript calls.
        all_ids = [v["id"] for v in videos]
        have_transcripts = storage.video_ids_with_transcripts(all_ids)
        to_do = [v for v in videos if v["id"] not in have_transcripts]
        if not to_do:
            print(f"All {len(videos)} videos already have transcripts.")
            _hint(
                "\U0001f4a1 All transcripts ready. Generate summaries:",
                f"   yt-artist summarize --artist-id {artist_id_arg}",
            )
            return
        url_base = "https://www.youtube.com/watch?v="
        concurrency = getattr(args, "concurrency", 1) or 1

        def _transcribe_one(v: dict) -> tuple[str, str | None]:
            """Worker: transcribe a single video. Returns (video_id, error_or_None)."""
            try:
                transcribe(
                    url_base + v["id"], storage,
                    artist_id=artist_id_arg, write_transcript_file=args.write_file,
                    data_dir=data_dir,
                )
                return (v["id"], None)
            except Exception as exc:  # noqa: BLE001
                return (v["id"], str(exc))

        # Warn if bulk transcribing 50+ videos without cookies (rate-limit risk)
        if len(to_do) >= 50 and not _quiet:
            from yt_artist.yt_dlp_util import get_auth_config
            auth = get_auth_config()
            if not auth["cookies_browser"] and not auth["cookies_file"]:
                _hint(
                    "\u26a0\ufe0f  Bulk transcription without cookies: YouTube may rate-limit after ~300 videos.",
                    "   For higher rate limits, set: export YT_ARTIST_COOKIES_BROWSER=chrome",
                    "   See: yt-artist doctor   or   USER_GUIDE.md 'Bulk transcription and rate limits'",
                )

        # Suggest --bg for large batches (foreground only)
        if not _bg_job_id:
            from yt_artist.jobs import maybe_suggest_background
            maybe_suggest_background(len(to_do), "transcribe", concurrency, sys.argv, quiet=_quiet)

        progress = _ProgressCounter(len(to_do), job_id=_bg_job_id, job_storage=_bg_storage)
        inter_delay = get_inter_video_delay()
        if concurrency > 1:
            log.info("Bulk transcribe: %d videos with %d workers (%.1fs inter-video delay).", len(to_do), concurrency, inter_delay)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures: dict = {}
            for i, v in enumerate(to_do):
                futures[pool.submit(_transcribe_one, v)] = v
                # Stagger submissions to avoid burst of concurrent yt-dlp requests.
                if inter_delay > 0 and i < len(to_do) - 1:
                    time.sleep(inter_delay)
            for fut in as_completed(futures):
                vid, err = fut.result()
                progress.tick("Transcribing", vid, error=err)
        total = time.monotonic() - progress.t0
        err_msg = f" ({progress.errors} errors)" if progress.errors else ""
        print(f"Transcribed: {progress.done} videos in {total:.0f}s.{err_msg}")
        progress.finalize(
            status="completed" if progress.errors == 0 else "completed",
            error_message=f"{progress.errors} errors" if progress.errors else None,
        )
        _hint(
            "\U0001f4a1 Next: generate summaries for all transcribed videos:",
            f"   yt-artist summarize --artist-id {artist_id_arg}",
        )
        return

    artist_id = None
    if args.write_file:
        vid = extract_video_id(video_url_or_id)
        v = storage.get_video(vid)
        artist_id = v["artist_id"] if v else None
    video_id = transcribe(
        video_url_or_id,
        storage,
        artist_id=artist_id,
        write_transcript_file=args.write_file,
        data_dir=data_dir,
    )
    print(f"Transcribed: {video_id}")
    _hint(
        "\U0001f4a1 Next: generate a summary:",
        f"   yt-artist summarize {video_id}",
    )


def _cmd_summarize(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """Per-video or bulk; ensures artist/video/transcript, reports dependencies; prompt from --prompt or artist default."""
    # Pre-flight: verify LLM endpoint is reachable before doing any expensive work.
    _check_llm()

    video_spec = (args.video or "").strip()
    artist_id_arg = (args.artist_id or "").strip()
    if video_spec and artist_id_arg:
        raise SystemExit("Provide either video (URL or id) or --artist-id, not both.")
    if not video_spec and not artist_id_arg:
        raise SystemExit("Provide video (URL or id) or --artist-id.")

    if artist_id_arg:
        # Bulk: summarize all transcribed videos for artist
        artist = storage.get_artist(artist_id_arg)
        if not artist:
            _report_dependency(f"artist {artist_id_arg} not in DB → fetching urllist.")
            channel_url = channel_url_for(artist_id_arg)
            path, count = fetch_channel(channel_url, storage, data_dir=data_dir)
            _report_dependency(f"Fetched urllist for {artist_id_arg} ({count} videos).")
        videos = storage.list_videos(artist_id=artist_id_arg)
        if not videos:
            raise SystemExit(f"No videos for artist {artist_id_arg}. Run urllist/fetch-channel first.")
        prompt_id = _resolve_prompt_id(storage, artist_id_arg, args.prompt_id)
        concurrency = getattr(args, "concurrency", 1) or 1

        # Batch DB check: one query for transcript existence instead of N.
        all_ids = [v["id"] for v in videos]
        have_transcripts = storage.video_ids_with_transcripts(all_ids)
        missing = [vid for vid in all_ids if vid not in have_transcripts]
        if missing:
            _report_dependency(f"{len(missing)} videos had no transcript → transcribing.")
            url_base = "https://www.youtube.com/watch?v="

            def _transcribe_missing(vid: str) -> tuple[str, str | None]:
                try:
                    transcribe(url_base + vid, storage, artist_id=artist_id_arg, write_transcript_file=False, data_dir=data_dir)
                    return (vid, None)
                except Exception as exc:  # noqa: BLE001
                    return (vid, str(exc))

            progress_t = _ProgressCounter(len(missing), job_id=_bg_job_id, job_storage=_bg_storage)
            inter_delay = get_inter_video_delay()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futs: dict = {}
                for i, vid in enumerate(missing):
                    futs[pool.submit(_transcribe_missing, vid)] = vid
                    if inter_delay > 0 and i < len(missing) - 1:
                        time.sleep(inter_delay)
                for fut in as_completed(futs):
                    vid, err = fut.result()
                    progress_t.tick("Transcribing", vid, error=err)

        # Batch DB check: one query for summary existence instead of N get_summaries_for_video.
        already_summarized = storage.video_ids_with_summary(all_ids, prompt_id)
        to_summarize = [v for v in videos if v["id"] not in already_summarized]
        skipped = len(videos) - len(to_summarize)

        if not to_summarize:
            print(f"All {len(videos)} videos already summarized with prompt '{prompt_id}'.")
            _hint(
                "\U0001f4a1 Try a different prompt for new perspectives:",
                "   yt-artist list-prompts",
                f"   yt-artist summarize --artist-id {artist_id_arg} --prompt <prompt-id>",
            )
            return

        # Suggest --bg for large batches (foreground only)
        if not _bg_job_id:
            total_work = len(missing) + len(to_summarize)
            from yt_artist.jobs import maybe_suggest_background
            maybe_suggest_background(total_work, "summarize", concurrency, sys.argv, quiet=_quiet)

        def _summarize_one(v: dict) -> tuple[str, str, str | None]:
            """Worker: summarize one video. Returns (video_id, summary_id_or_empty, error_or_None)."""
            try:
                sid = summarize(v["id"], prompt_id, storage, intent_override=args.intent, audience_override=args.audience)
                return (v["id"], sid, None)
            except Exception as exc:  # noqa: BLE001
                return (v["id"], "", str(exc))

        if concurrency > 1:
            log.info("Bulk summarize: %d videos with %d workers.", len(to_summarize), concurrency)
        progress_s = _ProgressCounter(len(to_summarize), job_id=_bg_job_id, job_storage=_bg_storage)
        done = 0
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_summarize_one, v): v for v in to_summarize}
            for fut in as_completed(futures):
                vid, summary_id, err = fut.result()
                progress_s.tick("Summarizing", vid, error=err)
                if not err:
                    done += 1
                    rows = storage.get_summaries_for_video(vid)
                    content = next((r["content"] for r in rows if r["prompt_id"] == prompt_id), "")
                    if args.max_preview > 0 and len(content) > args.max_preview:
                        preview = content[: args.max_preview] + "…"
                    else:
                        preview = content
                    print(f"Summary {summary_id}: {preview}")
        total_sum = time.monotonic() - progress_s.t0
        err_msg = f", {progress_s.errors} errors" if progress_s.errors else ""
        print(f"Summarized {done} new, {skipped} already done ({total_sum:.0f}s{err_msg}).")
        progress_s.finalize(
            status="completed" if progress_s.errors == 0 else "completed",
            error_message=f"{progress_s.errors} errors" if progress_s.errors else None,
        )
        _hint(
            "\U0001f4a1 Done! Browse your transcripts and summaries:",
            f"   yt-artist search-transcripts --artist-id {artist_id_arg}",
            "",
            "   Or try a custom prompt for different summaries:",
            "   yt-artist list-prompts",
        )
        return

    # Per-video: resolve video_id, ensure artist+video, ensure transcript, resolve prompt, summarize
    try:
        video_id = extract_video_id(video_spec)
    except ValueError:
        video_id = video_spec
    url = video_spec if video_spec.startswith("http") else f"https://www.youtube.com/watch?v={video_id}"
    t0 = time.monotonic()
    log.info("[1/4] Resolving artist and video…")
    artist_id, _ = ensure_artist_and_video_for_video_url(url, storage, data_dir)
    if not storage.get_transcript(video_id):
        log.info("[2/4] Transcribing video %s…", video_id)
        transcribe(url, storage, artist_id=artist_id, write_transcript_file=False, data_dir=data_dir)
    else:
        log.info("[2/4] Transcript already exists.")
    log.info("[3/4] Resolving prompt…")
    prompt_id = _resolve_prompt_id(storage, artist_id, args.prompt_id)
    log.info("[4/4] Generating summary with LLM…")
    summary_id = summarize(
        video_id,
        prompt_id,
        storage,
        intent_override=args.intent,
        audience_override=args.audience,
    )
    elapsed = time.monotonic() - t0
    log.info("Done in %.1fs.", elapsed)
    rows = storage.get_summaries_for_video(video_id)
    content = next((r["content"] for r in rows if r["prompt_id"] == prompt_id), "")
    if args.max_preview > 0 and len(content) > args.max_preview:
        content = content[: args.max_preview] + "..."
    print(f"Summary id: {summary_id}")
    print(content)
    _hint(
        "\U0001f4a1 Done! To summarize more videos from this channel:",
        f"   yt-artist summarize --artist-id {artist_id}",
        "",
        "   Or search your transcripts:",
        f"   yt-artist search-transcripts --artist-id {artist_id}",
    )


def _cmd_add_prompt(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """Add or update a prompt template; no dependency handling."""
    storage.upsert_prompt(
        prompt_id=args.prompt_id,
        name=args.name,
        template=args.template,
        artist_component=args.artist_component or None,
        video_component=args.video_component or None,
        intent_component=args.intent_component or None,
        audience_component=args.audience_component or None,
    )
    print(f"Prompt saved: id={args.prompt_id}, name={args.name}")
    _hint(
        "\U0001f4a1 Next: set this prompt as default for an artist:",
        f"   yt-artist set-default-prompt --artist-id @CHANNEL --prompt {args.prompt_id}",
        "",
        "   Or use it directly:",
        f"   yt-artist summarize VIDEO_ID --prompt {args.prompt_id}",
    )


def _cmd_list_prompts(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """List stored prompt templates."""
    rows = storage.list_prompts()
    if not rows:
        print("No prompts stored. Add one with: yt-artist add-prompt --id <id> --name <name> --template '...'")
        return
    for r in rows:
        template_preview = (r["template"][:60] + "...") if len(r["template"]) > 60 else r["template"]
        print(f"  {r['id']}: {r['name']}")
        print(f"    template: {template_preview}")
    _hint(
        "\U0001f4a1 Use a prompt: yt-artist summarize VIDEO_ID --prompt <id>",
        "   Or set a default: yt-artist set-default-prompt --artist-id @CHANNEL --prompt <id>",
    )


def _cmd_set_default_prompt(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """Set artist default prompt for summarize when --prompt is not passed."""
    artist_id = (args.artist_id or "").strip()
    prompt_id = (args.prompt_id or "").strip()
    if not storage.get_artist(artist_id):
        raise SystemExit(f"Artist {artist_id} not in DB. Run fetch-channel/urllist first.")
    if not storage.get_prompt(prompt_id):
        raise SystemExit(f"Prompt {prompt_id} not found. Run list-prompts to see available prompts.")
    storage.set_artist_default_prompt(artist_id, prompt_id)
    print(f"Default prompt for {artist_id} set to: {prompt_id}")
    _hint(
        f"\U0001f4a1 Next: summarize videos (will use '{prompt_id}' automatically):",
        f"   yt-artist summarize --artist-id {artist_id}",
    )


def _cmd_build_artist_prompt(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """Resolve artist, build 'about' (search/LLM), store on artist; optional --save-as-default creates prompt and sets default."""
    _check_llm()
    artist_id = (args.artist_id or "").strip()
    channel_url_arg = (args.channel_url or "").strip()
    artist = storage.get_artist(artist_id)
    if not artist and channel_url_arg:
        _report_dependency(f"artist {artist_id} not in DB → fetching urllist from {channel_url_arg}.")
        fetch_channel(channel_url_arg, storage, data_dir=data_dir)
        artist = storage.get_artist(artist_id)
    if not artist:
        raise SystemExit(f"Artist {artist_id} not in DB. Run fetch-channel/urllist or pass --channel-url.")
    channel_url = artist.get("channel_url") or channel_url_for(artist_id)
    name = artist.get("name") or artist_id
    log.info("Building about text (search + LLM)...")
    about = build_artist_about(artist_id, name, channel_url)
    storage.set_artist_about(artist_id, about)
    print(f"About saved for {artist_id} ({len(about)} chars).")
    if args.save_as_default:
        prompt_id = f"about-{artist_id.replace('@', '')}"
        template = f"Summarize the following transcript for a general audience. Artist context: {{artist}}\n\nVideo: {{video}}."
        storage.upsert_prompt(
            prompt_id=prompt_id,
            name=f"About-based summary for {name}",
            template=template,
            artist_component=about[:200],
        )
        storage.set_artist_default_prompt(artist_id, prompt_id)
        print(f"Created prompt '{prompt_id}' and set as default for {artist_id}.")
        _hint(
            "\U0001f4a1 Prompt is set! Now summarize:",
            f"   yt-artist summarize --artist-id {artist_id}",
        )
    else:
        _hint(
            "\U0001f4a1 Next: create a prompt from this about text:",
            f"   yt-artist build-artist-prompt --artist-id {artist_id} --save-as-default",
            "",
            "   Or summarize with the current default prompt:",
            f"   yt-artist summarize --artist-id {artist_id}",
        )


def _cmd_search_transcripts(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """List transcripts in DB, optionally filtered by artist-id or video-id."""
    rows = storage.list_transcripts(artist_id=args.artist_id, video_id=args.video_id)
    if not rows:
        print("No transcripts found.")
        _hint(
            "\U0001f4a1 Get started: fetch a channel and transcribe videos:",
            '   yt-artist fetch-channel "https://www.youtube.com/@CHANNEL"',
            "   yt-artist transcribe --artist-id @CHANNEL",
        )
        return
    print(f"{'VIDEO_ID':<16}\t{'ARTIST':<20}\t{'CHARS':>8}\t{'TITLE'}")
    for r in rows:
        title = (r.get("title") or "")[:50]
        if len((r.get("title") or "")) > 50:
            title += "..."
        print(f"{r['video_id']:<16}\t{r.get('artist_id', ''):<20}\t{r.get('transcript_len', 0):>8}\t{title}")
    sample_vid = rows[0]["video_id"]
    _hint(
        "\U0001f4a1 Summarize a transcript:",
        f"   yt-artist summarize {sample_vid}",
    )


def _cmd_quickstart(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """Guided tour: print copy-pasteable commands for the 3-step workflow."""
    db_flag = f" --db {args.db}" if args.db else ""
    w = 60
    print("=" * w)
    print("  yt-artist quickstart")
    print("=" * w)
    print()
    print("yt-artist works in 3 steps: fetch \u2192 transcribe \u2192 summarize")
    print()
    print("This walkthrough uses the @TED channel as an example.")
    print("A built-in 'default' prompt is ready to use \u2014 no setup needed.")
    print()
    print("-" * w)
    print("PREREQUISITE: Check your setup")
    print("-" * w)
    print()
    print(f"  yt-artist{db_flag} doctor")
    print()
    print("  This checks yt-dlp, YouTube authentication, and LLM connectivity.")
    print()
    print("  YouTube requires a PO token for subtitle downloads.")
    print("  yt-artist auto-installs a PO token provider (rustypipe) so")
    print("  tokens are generated automatically — no manual setup needed.")
    print()
    print("  If doctor reports a PO token warning, install the provider:")
    print("    pip install yt-dlp-get-pot-rustypipe")
    print()
    print("  Or set a manual token as fallback:")
    print("    export YT_ARTIST_PO_TOKEN=web.subs+<token>")
    print("  Guide: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide")
    print()
    print("-" * w)
    print("STEP 1: Fetch the channel's video list")
    print("-" * w)
    print()
    print(f'  yt-artist{db_flag} fetch-channel "{_DEMO_CHANNEL_URL}"')
    print()
    print("  This downloads the list of all videos and stores them in the DB.")
    print("  Large channels (1000+ videos) may take a few minutes.")
    print()
    print("-" * w)
    print("STEP 2: Transcribe a video")
    print("-" * w)
    print()
    print(f'  yt-artist{db_flag} transcribe "{_DEMO_VIDEO_URL}"')
    print()
    print("  This downloads subtitles (or auto-captions) for one video.")
    print(f"  For all videos: yt-artist{db_flag} transcribe --artist-id {_DEMO_CHANNEL}")
    print()
    print("-" * w)
    print("STEP 3: Summarize")
    print("-" * w)
    print()
    print(f"  yt-artist{db_flag} summarize {_DEMO_VIDEO_ID}")
    print()
    print("  This generates an AI summary using the default prompt.")
    print("  (Requires Ollama running locally, or set OPENAI_API_KEY)")
    print(f"  For all videos: yt-artist{db_flag} summarize --artist-id {_DEMO_CHANNEL}")
    print()
    print("-" * w)
    print("SHORTCUT: summarize does everything automatically!")
    print("-" * w)
    print()
    print(f'  yt-artist{db_flag} summarize "{_DEMO_VIDEO_URL}"')
    print()
    print("  This single command auto-creates the artist, fetches the video,")
    print("  downloads the transcript, and generates the summary.")
    print()
    print("=" * w)
    print("  Copy any command above and paste it in your terminal to start!")
    print("=" * w)


def _cmd_doctor(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """Pre-flight checks: yt-dlp, YouTube auth, PO token, LLM, test subtitle fetch."""
    import shutil
    import subprocess

    ok_count = 0
    warn_count = 0
    fail_count = 0

    def _ok(msg: str) -> None:
        nonlocal ok_count
        ok_count += 1
        print(f"  \u2705 OK   {msg}")

    def _warn(msg: str) -> None:
        nonlocal warn_count
        warn_count += 1
        print(f"  \u26a0\ufe0f  WARN {msg}")

    def _fail(msg: str) -> None:
        nonlocal fail_count
        fail_count += 1
        print(f"  \u274c FAIL {msg}")

    print("yt-artist doctor")
    print("=" * 50)
    print()

    # --- [1/5] yt-dlp installation ---
    print("[1/5] yt-dlp installation")
    yt_dlp_path = shutil.which("yt-dlp")
    if yt_dlp_path:
        try:
            ver = subprocess.run(
                ["yt-dlp", "--version"], capture_output=True, text=True, timeout=10,
            )
            version_str = (ver.stdout or "").strip()
            _ok(f"yt-dlp found: {yt_dlp_path} (version {version_str})")
        except Exception:
            _ok(f"yt-dlp found: {yt_dlp_path} (could not get version)")
    else:
        _fail("yt-dlp not found on PATH. Install: pip install yt-dlp")
    print()

    # --- [2/5] YouTube authentication (cookies) ---
    print("[2/5] YouTube authentication")
    auth = get_auth_config()
    if auth["cookies_browser"]:
        _ok(f"Cookies: using browser '{auth['cookies_browser']}'")
    elif auth["cookies_file"]:
        _ok(f"Cookies: using file '{auth['cookies_file']}'")
    else:
        _warn(
            "No cookies configured. Some videos (age-restricted, members-only) may fail.\n"
            "         Cookies also help avoid rate limits during bulk transcription (50+ videos).\n"
            "         Set YT_ARTIST_COOKIES_BROWSER=chrome  (or firefox/safari)"
        )
    print()

    # --- [3/5] PO token ---
    print("[3/5] PO token (proof of origin)")
    has_provider = False
    try:
        from importlib.metadata import distribution
        distribution("yt-dlp-get-pot-rustypipe")
        has_provider = True
    except Exception:
        pass
    if auth["po_token"]:
        extra = " (auto-provider also installed)" if has_provider else ""
        _ok(f"PO token is set via YT_ARTIST_PO_TOKEN{extra}")
    elif has_provider:
        _ok("PO token provider installed (yt-dlp-get-pot-rustypipe) — tokens generated automatically")
    else:
        _warn(
            "No PO token and no auto-provider installed. Transcribe will likely fail.\n"
            "         Fix: pip install yt-dlp-get-pot-rustypipe   (recommended, automatic)\n"
            "         Or:  export YT_ARTIST_PO_TOKEN=web.subs+<token>   (manual)\n"
            "         Guide: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide"
        )
    print()

    # --- [4/5] LLM endpoint ---
    print("[4/5] LLM endpoint (for summarize)")
    from yt_artist.llm import get_config_summary, check_connectivity
    llm = get_config_summary()
    provider = "Ollama (local)" if llm["is_ollama"] else "OpenAI-compatible API"
    print(f"       Provider: {provider}")
    print(f"       Endpoint: {llm['base_url']}")
    print(f"       Model:    {llm['model']}")
    try:
        check_connectivity()
        _ok(f"LLM endpoint reachable ({llm['base_url']})")
    except RuntimeError as e:
        _fail(f"LLM endpoint unreachable. {e}")
    print()

    # --- [5/5] Test subtitle fetch ---
    print("[5/5] Test subtitle fetch (quick metadata check)")
    if yt_dlp_path:
        test_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo" — first YT video
        try:
            from yt_artist.yt_dlp_util import yt_dlp_cmd
            cmd = yt_dlp_cmd() + ["--skip-download", "--no-warnings", "-j", test_url]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                _ok("yt-dlp can reach YouTube and fetch video metadata")
            else:
                stderr = (result.stderr or "").strip()
                from yt_artist.transcriber import _classify_yt_dlp_error
                err_type, err_msg = _classify_yt_dlp_error(stderr)
                if err_type != "generic":
                    _fail(f"YouTube access issue: {err_msg}")
                else:
                    _fail(f"yt-dlp metadata fetch failed (exit {result.returncode}): {stderr[:200]}")
        except subprocess.TimeoutExpired:
            _warn("yt-dlp metadata fetch timed out (30s). Network may be slow.")
        except Exception as e:
            _fail(f"yt-dlp test failed: {e}")
    else:
        _warn("Skipped (yt-dlp not installed)")
    print()

    # --- Summary ---
    print("=" * 50)
    total = ok_count + warn_count + fail_count
    print(f"  {ok_count}/{total} checks passed", end="")
    if warn_count:
        print(f", {warn_count} warning(s)", end="")
    if fail_count:
        print(f", {fail_count} failure(s)", end="")
    print()
    if fail_count or warn_count:
        print()
        print("  Fix warnings/failures above, then re-run: yt-artist doctor")


def _cmd_jobs(args: argparse.Namespace, storage: Storage, data_dir: Path) -> None:
    """Handle jobs subcommands: list, attach, stop, clean."""
    from yt_artist.jobs import list_jobs, attach_job, stop_job, cleanup_old_jobs

    action = getattr(args, "jobs_action", "list") or "list"

    if action == "list":
        rows = list_jobs(storage)
        if not rows:
            print("No background jobs.")
            return
        print(f"{'ID':>10}  {'STATUS':<10}  {'PROGRESS':<12}  {'STARTED':<20}  {'COMMAND'}")
        for r in rows:
            jid = r["id"][:8]
            status = r["status"]
            total = r.get("total", 0) or 0
            done = r.get("done", 0) or 0
            progress = f"{done}/{total}" if total > 0 else "-"
            started = (r.get("started_at") or "")[:19]
            command = (r.get("command") or "")[:50]
            print(f"{jid:>10}  {status:<10}  {progress:<12}  {started:<20}  {command}")

    elif action == "attach":
        attach_job(storage, args.job_id)

    elif action == "stop":
        stop_job(storage, args.job_id)

    elif action == "clean":
        removed = cleanup_old_jobs(storage)
        print(f"Cleaned up {removed} old job(s).")


if __name__ == "__main__":
    main()
