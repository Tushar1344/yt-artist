# ADR-0010: YouTube rate-limit safety

## Status

Accepted (2026-02-09).

## Context

Bulk operations (100+ videos) risk triggering YouTube's rate limits, resulting in 429 errors, IP throttling, or temporary bans. The tool needs to be a good citizen of YouTube's infrastructure while still being useful for large channels.

## Decision

### Conservative concurrency defaults

- `YT_ARTIST_MAX_CONCURRENCY` defaults to 2 (not unlimited).
- `get_max_concurrency()` returns the configured value, capped to a safe maximum.

### Inter-video delay

- `YT_ARTIST_INTER_VIDEO_DELAY` defaults to 2.0 seconds between videos in bulk operations.
- Applied between each video in transcribe and summarize bulk loops.
- Configurable via environment variable; negative values clamped to 0.

### yt-dlp sleep flags

- `--sleep-requests 1.5` and `--sleep-subtitles 2` passed to all yt-dlp calls by default.
- Configurable via `YT_ARTIST_SLEEP_REQUESTS` and `YT_ARTIST_SLEEP_SUBTITLES`.

### Optimistic subtitle download

- Transcriber tries `--write-auto-subs` first (YouTube auto-generated captions); if unavailable, falls back to `--write-subs` (manual captions).
- This avoids the common failure mode of requesting manual subs for videos that only have auto-generated ones.

### Exponential backoff on 429

- When yt-dlp returns a 429-like error, retry with exponential backoff (configurable base delay).
- Protects against transient rate limit responses.

### Cookie support

- `YT_ARTIST_COOKIES_BROWSER` and `YT_ARTIST_COOKIES_FILE` environment variables pass authentication cookies to yt-dlp.
- Enables access to age-restricted and member-only content.
- Browser cookies take precedence over cookie files when both are set.

## Consequences

- Safe by default: new users won't accidentally hammer YouTube.
- Power users can tune all parameters via environment variables.
- Cookie support enables access to restricted content without modifying code.
- 12 new tests cover all rate-limit safety configurations.
