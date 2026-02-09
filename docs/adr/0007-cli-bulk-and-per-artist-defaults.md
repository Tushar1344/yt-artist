# ADR-0007: CLI bulk operations and per-artist default prompt

## Status

Accepted (2026-02-08).

## Context

Users wanted a simpler CLI: bulk urllist, bulk transcribe, bulk summarize per artist; single summarize command for both per-video and bulk; prompt as an override with a default per artist; and automatic handling of missing data (urllist, transcripts) with clear feedback.

## Decision

- **One summarize command:** `summarize [video_url_or_id | --artist-id ARTIST_ID] [--prompt ID]`. Per-video: resolve video, ensure artist+video in DB (fetch channel if needed), ensure transcript, then summarize. Bulk: ensure artist/videos (fetch if needed), ensure transcripts for each, then summarize each. Prompt resolution: `--prompt` else artist default else `YT_ARTIST_DEFAULT_PROMPT` else first prompt in DB.
- **Per-artist default prompt:** New columns on `artists`: `default_prompt_id`, `about`. Set default via `set-default-prompt --artist-id @X --prompt ID`. Summarizer uses artist’s `about` for the `{artist}` placeholder when present.
- **Bulk transcribe:** `transcribe --artist-id @X` transcribes all videos for that artist; if artist or videos are missing, fetch channel first and report “Dependencies: …”.
- **Dependency messaging:** Whenever the tool auto-creates urllist or transcripts, print one short line: “Dependencies: …” so the user knows what was done.
- **build-artist-prompt:** New command to search (optional duckduckgo-search) and build “about” text; store on artist; optionally create a prompt and set as artist default.
- **Fetcher:** Add `get_channel_info_for_video` and `ensure_artist_and_video_for_video_url` so per-video summarize can add the artist and video to the DB when they are missing.

## Consequences

- Single entry point for summarize simplifies UX; dependency fill and messages keep behavior predictable.
- Per-artist default prompt and optional “about” improve summarization without requiring `--prompt` every time.
- Existing DBs get new columns via migration in `Storage.ensure_schema()`; no separate migration script required.
- MCP server is unchanged in this iteration; it can be updated later to mirror bulk operations.
