"""Producer-consumer pipeline: transcribe and summarize run concurrently.

When bulk summarize discovers missing transcripts, instead of transcribing all
then summarizing all (sequential), this module overlaps the two phases.  As each
transcript lands in the DB, the summarize poller picks it up.

Coordination is via DB-polling (per ADR-0012): simpler, idempotent,
crash-recoverable.  No in-memory queue to lose on crash.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, List, Set, Tuple

log = logging.getLogger("yt_artist.pipeline")

DEFAULT_POLL_INTERVAL: float = 5.0


@dataclass
class PipelineResult:
    """Outcome of a pipeline run."""

    transcribed: int = 0
    transcribe_errors: int = 0
    summarized: int = 0
    summarize_errors: int = 0
    scored: int = 0
    score_errors: int = 0
    elapsed: float = 0.0


def _split_concurrency(total: int) -> Tuple[int, int]:
    """Split concurrency budget between transcribe and summarize workers.

    Returns (transcribe_workers, summarize_workers).

    Transcribe gets more workers because YouTube I/O is the slower bottleneck.
    Summarize is faster per-video and not rate-limited by the LLM.

    concurrency=1 → (1, 1): overlap is the whole point; YouTube pressure stays at 1.
    concurrency=2 → (1, 1): same split.
    concurrency=3 → (2, 1): extra worker goes to transcribe.
    """
    if total <= 2:
        return (1, 1)
    return (total - 1, 1)


def run_pipeline(
    *,
    video_ids_to_transcribe: List[str],
    video_ids_to_summarize: List[str],
    transcribe_fn: Callable[[str], Tuple[str, str | None]],
    summarize_fn: Callable[[str], Tuple[str, str, str | None]],
    poll_fn: Callable[[], List[str]],
    transcribe_workers: int = 1,
    summarize_workers: int = 1,
    inter_delay: float = 2.0,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    transcribe_progress: Any = None,
    summarize_progress: Any = None,
    score_fn: Callable[[str], Tuple[str, str | None]] | None = None,
    score_poll_fn: Callable[[], List[str]] | None = None,
    score_progress: Any = None,
) -> PipelineResult:
    """Run transcribe→summarize→score pipeline with overlapping execution.

    Producer: transcribe workers process *video_ids_to_transcribe*.
    Consumer: summarize workers process *video_ids_to_summarize* immediately,
              plus poll for newly transcribed videos via *poll_fn*.
    Scorer (optional): polls for summarized-but-unscored videos via *score_poll_fn*
              and runs *score_fn* on each.  Activated only when both are provided.

    *poll_fn* should return video IDs that have transcripts but no summaries.
    *score_poll_fn* should return video IDs that have summaries but no quality score.
    Progress counters are optional ``_ProgressCounter`` instances from cli.py
    (typed ``Any`` to avoid circular imports).
    """
    t0 = time.monotonic()
    result = PipelineResult()

    all_summarizable: Set[str] = set(video_ids_to_transcribe) | set(video_ids_to_summarize)
    submitted: Set[str] = set()
    submitted_lock = threading.Lock()
    producer_done = threading.Event()

    # Protect result counter increments from concurrent workers.
    result_lock = threading.Lock()

    log.info(
        "Pipeline: %d to transcribe, %d to summarize immediately, %d total. Workers: %d transcribe, %d summarize.",
        len(video_ids_to_transcribe),
        len(video_ids_to_summarize),
        len(all_summarizable),
        transcribe_workers,
        summarize_workers,
    )

    summarize_pool = ThreadPoolExecutor(max_workers=summarize_workers)
    summarize_futures: List[Future] = []
    futures_lock = threading.Lock()

    # -- helpers ---------------------------------------------------------------

    def _submit_summarize(vid: str) -> None:
        """Submit a video for summarization (thread-safe)."""

        def _worker() -> None:
            vid_id, *rest = summarize_fn(vid)
            err = rest[-1] if rest else None
            with result_lock:
                if err:
                    result.summarize_errors += 1
                else:
                    result.summarized += 1
            if summarize_progress is not None:
                summarize_progress.tick("Pipeline:Summarizing", vid_id, error=err)

        with futures_lock:
            fut = summarize_pool.submit(_worker)
            summarize_futures.append(fut)

    # -- immediate summarize work (videos already transcribed, need summaries) -

    for vid in video_ids_to_summarize:
        with submitted_lock:
            submitted.add(vid)
        _submit_summarize(vid)

    # -- consumer poller thread ------------------------------------------------

    def _poller() -> None:
        """Poll DB for newly transcribed videos and submit summarize work."""
        while not producer_done.is_set():
            try:
                ready = poll_fn()
            except Exception:
                log.debug("Poll error, retrying", exc_info=True)
                time.sleep(poll_interval)
                continue
            with submitted_lock:
                new_work = [v for v in ready if v in all_summarizable and v not in submitted]
                for v in new_work:
                    submitted.add(v)
            for v in new_work:
                _submit_summarize(v)
            # Sleep in small increments so we notice producer_done quickly.
            deadline = time.monotonic() + poll_interval
            while time.monotonic() < deadline and not producer_done.is_set():
                time.sleep(min(0.5, deadline - time.monotonic()))

        # Final poll after producer is done — catch stragglers.
        try:
            ready = poll_fn()
        except Exception:
            log.debug("Final poll error", exc_info=True)
            return
        with submitted_lock:
            final = [v for v in ready if v in all_summarizable and v not in submitted]
            for v in final:
                submitted.add(v)
        for v in final:
            _submit_summarize(v)

    poller_thread = threading.Thread(target=_poller, daemon=True, name="pipeline-poller")
    poller_thread.start()

    # -- producer: transcribe pool (blocking until all transcriptions done) ----

    with ThreadPoolExecutor(max_workers=transcribe_workers) as transcribe_pool:
        transcribe_futures: dict = {}
        for i, vid in enumerate(video_ids_to_transcribe):
            fut = transcribe_pool.submit(transcribe_fn, vid)
            transcribe_futures[fut] = vid
            if inter_delay > 0 and i < len(video_ids_to_transcribe) - 1:
                time.sleep(inter_delay)

        for fut in as_completed(transcribe_futures):
            vid_id, err = fut.result()
            with result_lock:
                if err:
                    result.transcribe_errors += 1
                else:
                    result.transcribed += 1
            if transcribe_progress is not None:
                transcribe_progress.tick("Pipeline:Transcribing", vid_id, error=err)

    # -- signal producer done, wait for consumer to drain ----------------------

    producer_done.set()
    poller_thread.join(timeout=poll_interval + 10)
    summarize_pool.shutdown(wait=True)

    # -- Stage 3: scoring (optional) -------------------------------------------

    enable_scoring = score_fn is not None and score_poll_fn is not None
    if enable_scoring:
        log.info("Pipeline: scoring stage starting.")
        score_submitted: Set[str] = set()

        # Run scoring in a single worker — calls are tiny (~100 tokens)
        with ThreadPoolExecutor(max_workers=1) as score_pool:
            score_futures: List[Future] = []

            def _score_worker(vid: str) -> None:
                vid_id, err = score_fn(vid)  # type: ignore[misc]
                with result_lock:
                    if err:
                        result.score_errors += 1
                    else:
                        result.scored += 1
                if score_progress is not None:
                    score_progress.tick("Pipeline:Scoring", vid_id, error=err)

            # Poll for all unscored summaries and score them
            try:
                unscored = score_poll_fn()  # type: ignore[misc]
            except Exception:
                log.debug("Score poll error", exc_info=True)
                unscored = []

            for vid in unscored:
                if vid not in score_submitted:
                    score_submitted.add(vid)
                    score_futures.append(score_pool.submit(_score_worker, vid))

            # Wait for all scoring to finish
            for fut in score_futures:
                try:
                    fut.result()
                except Exception:
                    log.debug("Score future error", exc_info=True)

        log.info("Pipeline: scoring done — %d scored, %d errors.", result.scored, result.score_errors)

    result.elapsed = time.monotonic() - t0
    return result
