# ADR-0001: Use yt-dlp for channel listing and transcripts

**Status:** Accepted  
**Date:** 2026-02-08  
**Deciders:** Implementation + plan

## Context

We need to get (1) all video URLs for a YouTube channel and (2) per-video transcripts. Options: YouTube Data API v3 (quota, key), youtube-transcript-api, or yt-dlp.

## Decision

Use **yt-dlp** for both: channel/playlist listing via `--flat-playlist` and transcripts via `--write-auto-sub` / `--write-sub`. No API key; one dependency; supports channels and playlists.

## Consequences

- Positive: No quota, works offline after fetch, single tool for URLs + subs.
- Negative: Depends on yt-dlp keeping up with YouTube changes; subprocess or library API.
- Follow-ups: Document minimum yt-dlp version in README.

## Links

- Plan: section 3 (Tool selection)
- Scratch: docs/scratch/SCRATCH.md#2-fetcher
