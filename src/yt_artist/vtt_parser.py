"""VTT/SRT timestamp parsing — extract structured segments from raw subtitle content."""

from __future__ import annotations

import logging
import re
from typing import Dict, List

log = logging.getLogger("yt_artist.vtt_parser")

# Regex for VTT/SRT timestamp lines: 00:00:00.000 --> 00:00:05.000
_TS_LINE_RE = re.compile(
    r"(\d{2}:\d{2}(?::\d{2})?[.,]\d{3})"  # start timestamp
    r"\s*-->\s*"
    r"(\d{2}:\d{2}(?::\d{2})?[.,]\d{3})"  # end timestamp
)

# Inline VTT tags to strip: <00:00:00.000>, <c>, </c>, etc.
_INLINE_TAG_RE = re.compile(r"<[^>]+>")


def _parse_timestamp(ts: str) -> float:
    """Convert VTT/SRT timestamp to seconds.

    Supports:
      - HH:MM:SS.mmm  (VTT/SRT full)
      - HH:MM:SS,mmm  (SRT comma variant)
      - MM:SS.mmm      (VTT short)
    """
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return 0.0


def _clean_cue_text(text: str) -> str:
    """Strip VTT inline tags and cue settings from text."""
    text = _INLINE_TAG_RE.sub("", text)
    return text.strip()


def parse_timestamped_segments(
    raw_vtt: str,
    fmt: str = "vtt",
) -> List[Dict[str, object]]:
    """Parse raw VTT/SRT content into structured segments.

    Returns list of dicts: ``[{"start_sec": float, "end_sec": float, "text": str}, ...]``

    Deduplicates consecutive segments with identical text (common in auto-captions).
    Supports VTT and SRT formats.  Returns empty list for unrecognised formats.
    """
    if not raw_vtt or not raw_vtt.strip():
        return []

    if fmt not in ("vtt", "srt"):
        log.debug("Unsupported subtitle format %r — returning empty segments", fmt)
        return []

    segments: List[Dict[str, object]] = []
    lines = raw_vtt.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Look for a timestamp line
        m = _TS_LINE_RE.search(line)
        if m:
            start_sec = _parse_timestamp(m.group(1))
            end_sec = _parse_timestamp(m.group(2))

            # Collect text lines until next blank line or next timestamp
            text_parts: list[str] = []
            i += 1
            while i < len(lines):
                tl = lines[i].strip()
                if not tl:
                    break
                if _TS_LINE_RE.search(tl):
                    break
                # Skip SRT sequence numbers (standalone digit lines)
                if re.match(r"^\d+$", tl):
                    break
                # Skip VTT cue settings lines
                if re.match(r"^\s*(?:align|position|line|size):", tl):
                    i += 1
                    continue
                text_parts.append(_clean_cue_text(tl))
                i += 1

            text = " ".join(t for t in text_parts if t)
            if not text:
                continue

            # Deduplicate consecutive identical text
            if segments and segments[-1]["text"] == text:
                # Extend end time of previous segment
                segments[-1]["end_sec"] = end_sec
            else:
                segments.append(
                    {
                        "start_sec": round(start_sec, 3),
                        "end_sec": round(end_sec, 3),
                        "text": text,
                    }
                )
        else:
            i += 1

    return segments
