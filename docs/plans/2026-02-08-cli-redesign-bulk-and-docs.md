# CLI redesign: bulk/single ops, per-artist defaults, auto-dependencies

**Date:** 2026-02-08

## Summary

- **One summarize command:** `summarize [video | --artist-id @X] [--prompt ID]` for per-video or bulk; dependencies (artist, transcript) auto-created and reported.
- **Per-artist default prompt:** Stored on `artists`; resolved when `--prompt` is not passed. Set via `set-default-prompt`.
- **Bulk transcribe:** `transcribe --artist-id @X`; fetches urllist if artist/videos missing.
- **Urllist alias:** `urllist` as alias for `fetch-channel`.
- **build-artist-prompt:** Search (optional duckduckgo-search) + “about” text stored on artist; optional `--save-as-default` to create and set a prompt.
- **Dependency messaging:** Short “Dependencies: …” lines when urllist or transcripts are auto-created.
- **Schema:** `artists.default_prompt_id`, `artists.about`; migration in storage for existing DBs.
- **Fetcher:** `get_channel_info_for_video`, `ensure_artist_and_video_for_video_url` for per-video summarize.

## Implementation order used

1. Schema + storage (columns, migration, upsert_artist, get/set default prompt and about)
2. Fetcher (channel from video, ensure_artist_and_video_for_video_url)
3. CLI single summarize with prompt resolution and dependency fill
4. CLI transcribe --artist-id and dependency fill
5. set-default-prompt and wire artist default
6. build-artist-prompt (search, about, --save-as-default)
7. Documentation (USER_GUIDE, README, plans, ADR, skill, in-code)

## References

- [ADR 0007](../adr/0007-cli-bulk-and-per-artist-defaults.md)
- [USER_GUIDE.md](../../USER_GUIDE.md) — command reference and concepts
