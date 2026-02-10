# yt-artist — User Guide

A portable command-line utility for Mac that fetches YouTube channel video lists, transcribes videos, and generates AI summaries from transcripts. Use it from the terminal to bulk-pull URLs per artist, transcribe per video or in bulk per artist, and summarize per video or in bulk. Missing dependencies (urllist, transcripts) are auto-created and reported.

---

## What is yt-artist?

- **Bulk urllist per artist:** Give it a channel URL and it fetches all video URLs into a markdown file and stores artist + videos in SQLite.
- **Transcribe:** One video (by URL or id) or all videos for an artist (bulk). If the artist or videos are missing, it fetches the urllist first. Parallel execution for speed.
- **Summarize:** One video (by URL or id) or all transcribed videos for an artist (bulk). Uses your prompt or the artist's default prompt. If the transcript is missing, it transcribes first; if the artist/video aren't in the DB, it adds them. Parallel execution for speed.
- **Background jobs:** Push long-running bulk operations to the background with `--bg`. Monitor progress with `yt-artist jobs`. Attach to a running job's log, stop jobs, or clean up old ones.
- **Per-artist default prompt:** Set a default prompt per artist so you can run `summarize` without passing `--prompt` every time.
- **Build artist prompt:** Optionally search the web for the artist and build an "about" text (and a prompt) so summaries are artist-aware.
- **Guided onboarding:** After every command, yt-artist suggests what to do next. Run `yt-artist quickstart` for a guided walkthrough. Use `--quiet`/`-q` to suppress hints.
- **Rate-limit safe:** Conservative delays between YouTube requests. Configurable concurrency, sleep intervals, and cookie support for restricted content.
- **Portable:** Single install script for Mac; everything lives under `~/.local/yt-artist` and `~/.local/bin`.

---

## Prerequisites

- **macOS** (Intel or Apple Silicon).
- **Python 3.9+** — Pre-installed on recent macOS, or install from [python.org](https://www.python.org/downloads/) or Homebrew (`brew install python`).
- **yt-dlp** — The install script installs it as a dependency.
- **Ollama (optional but recommended)** — For local AI summaries. Install from [ollama.com](https://ollama.com), then run e.g. `ollama run mistral`.
- **duckduckgo-search (optional)** — For `build-artist-prompt` web search. Install with `pip install duckduckgo-search` or `pip install yt-artist[search]`.
- After install, run `yt-artist doctor` to verify YouTube auth (PO token) and LLM.

---

## Install (portable on Mac) — terminal only

1. **Clone the repo:**
   ```bash
   git clone <repo-url> yt-artist
   cd yt-artist
   ```

2. **Run the install script:**
   ```bash
   ./scripts/install-mac.sh
   ```
   The script creates a venv, installs yt-artist and dependencies, puts `yt-artist` in `~/.local/bin`, and adds it to your PATH in `~/.zshrc` (or `~/.bash_profile`) if needed.

3. **In new terminals** (if the script added PATH), run once: `source ~/.zshrc` (or `source ~/.bash_profile`).

4. **Check:** `yt-artist --help`

---

## Not working in Mac terminal?

Use the **repo launcher** (no PATH needed). From the repo root:

```bash
./scripts/yt-artist.sh --help
./scripts/yt-artist.sh --db "$DB" list-prompts
```

---

## Quick start

```bash
cd ~/my-yt-data
export DB=./yt.db
```

1. **Verify your setup** (yt-dlp, PO token, LLM):
   ```bash
   yt-artist doctor
   ```

2. **Bulk urllist for a channel (optional; summarize can fetch it for you):**
   ```bash
   yt-artist --db "$DB" --data-dir . fetch-channel "https://www.youtube.com/@hubermanlab"
   ```
   Or use the alias: `yt-artist --db "$DB" urllist "https://www.youtube.com/@hubermanlab"`

3. **Transcribe one video or all videos for an artist:**
   ```bash
   yt-artist --db "$DB" transcribe "https://www.youtube.com/watch?v=bdsc3Spm6Sw"
   yt-artist --db "$DB" transcribe --artist-id @hubermanlab
   ```
   If the artist or videos aren’t in the DB, you’ll see: `Dependencies: artist/videos missing → fetching urllist...` then transcribing.

4. **Add a prompt and set it as default for an artist:**
   ```bash
   yt-artist --db "$DB" add-prompt --id short --name "Short summary" \
     --template "Summarize in 2-3 sentences for {audience}. Artist: {artist}. Video: {video}." \
     --audience-component "general"
   yt-artist --db "$DB" set-default-prompt --artist-id @hubermanlab --prompt short
   ```

5. **Summarize one video or all transcribed videos for an artist:**
   ```bash
   yt-artist --db "$DB" summarize "https://www.youtube.com/watch?v=bdsc3Spm6Sw"
   yt-artist --db "$DB" summarize bdsc3Spm6Sw --prompt short
   yt-artist --db "$DB" summarize --artist-id @hubermanlab
   ```
   If the artist/videos or transcripts are missing, you’ll see short “Dependencies: …” lines; then the tool creates them and continues.

6. **Optional: build "about" text for an artist from web search and set as default prompt:**
   ```bash
   yt-artist --db "$DB" build-artist-prompt --artist-id @hubermanlab --save-as-default
   ```
   Install `duckduckgo-search` for better results: `pip install duckduckgo-search` or `pip install yt-artist[search]`.

---

## Command reference

| Command | Description |
|--------|-------------|
| `fetch-channel` / `urllist` \<channel_url\> | Bulk-pull all video URLs for the channel; writes urllist and updates DB. Large channels (1000+ videos) may take a few minutes. |
| `transcribe` [video_url \| --artist-id @X] | Per-video: transcribe one video. Bulk: transcribe all videos for the artist (fetches urllist if missing). Optional `--write-file`. |
| `summarize` [video \| --artist-id @X] [--prompt ID] | Per-video: summarize one video (adds artist/video/transcript if missing). Bulk: summarize all transcribed videos for the artist. Prompt: `--prompt` else artist default else `YT_ARTIST_DEFAULT_PROMPT` else first prompt. |
| `set-default-prompt --artist-id @X --prompt ID` | Set the default prompt for an artist (used when `--prompt` is not passed to summarize). |
| `build-artist-prompt --artist-id @X [--channel-url URL] [--save-as-default]` | Search and build “about” text for the artist; store in DB. Optional: create a prompt and set as artist default. Optional dependency: duckduckgo-search. |
| `add-prompt --id ID --name NAME --template "..."` | Define a prompt template (placeholders: `{artist}`, `{video}`, `{intent}`, `{audience}`). |
| `list-prompts` | List stored prompt templates. |
| `search-transcripts` [--artist-id ID] [--video-id ID] | List transcripts in the DB; optionally filter. |
| `jobs` | List all background jobs with progress (ID, status, done/total, command). |
| `jobs attach <job_id>` | Tail the log of a job. Press Ctrl-C to detach (job keeps running). |
| `jobs stop <job_id>` | Send SIGTERM to stop a running background job. |
| `jobs clean` | Remove finished jobs older than 7 days and their log files. |
| `quickstart` | Print a guided 3-step walkthrough using @TED as an example. |
| `doctor` | Check your setup: yt-dlp installation, YouTube authentication, PO token, LLM endpoint, test metadata fetch. |

**Global options:** `--db PATH`, `--data-dir PATH`, `--bg` (run in background), `-q`/`--quiet` (suppress hints)

> **Note:** Global options must appear **before** the subcommand. For example: `yt-artist --quiet summarize ...` (correct), not `yt-artist summarize ... --quiet` (error).

---

## Concepts

- **Per-artist default prompt:** Each artist can have a default prompt. When you run `summarize --artist-id @X` (or summarize a video that belongs to that artist) without `--prompt`, that default is used. Set it with `set-default-prompt`.
- **Dependency chain:** artist → urllist/videos in DB → transcripts → summaries. Commands that need a downstream step (e.g. summarize) auto-create upstream data (urllist, transcripts) when missing.
- **"Dependencies: …" messages:** When the tool auto-creates something (e.g. urllist or transcripts), it prints one short line so you know what was done, e.g. `Dependencies: artist/videos missing → fetched urllist for @NateBJones (42 videos).`
- **Background jobs:** When processing 5+ videos, yt-artist suggests running in the background. Add `--bg` to any bulk command to detach it. The job runs as a separate process; you can close your terminal and it keeps going. Use `yt-artist jobs` to check progress, `jobs attach <id>` to tail the log, or `jobs stop <id>` to cancel. Job IDs are short (first 8 hex chars shown); prefix matching works.
- **Next-step hints:** After each command, yt-artist prints a hint to stderr suggesting what to do next. For example, after `fetch-channel`, it suggests `transcribe`. Use `--quiet` to suppress all hints.
- **Parallel execution:** Bulk transcribe and summarize process videos in parallel (default: 2 workers). Control with `YT_ARTIST_MAX_CONCURRENCY`.
- **Rate-limit safety:** yt-dlp requests include sleep intervals between requests. Inter-video delay (default 2s) prevents hammering YouTube. All configurable via environment variables.

---

## Where data lives

- **Database:** Path you pass with `--db` (e.g. `./yt.db`). Default: `<data-dir>/data/yt_artist.db`.
- **Urllists:** `<data-dir>/data/artists/<artist_id>/artist<id><name>-urllist.md`.
- **Transcript files (optional):** With `transcribe --write-file`, under `<data-dir>/data/artists/<artist_id>/transcripts/<video_id>.txt`.
- **Summaries:** In the SQLite DB only. Existing DBs may need a one-time migration (new columns on `artists`); the tool adds them automatically when you run any command.
- **Job logs:** `<data-dir>/data/jobs/<job_id>.log` — log files for background jobs. Cleaned up with `yt-artist jobs clean`.

---

## Environment (summaries / LLM)

- **YT_ARTIST_DEFAULT_PROMPT** — Default prompt id when the artist has no default and you don't pass `--prompt`.
- **Local Ollama (default):** If `OPENAI_API_KEY` is not set, yt-artist uses `http://localhost:11434/v1` and model **mistral**.
- **Force local Ollama when you have an API key:** Set `OPENAI_BASE_URL=http://localhost:11434/v1` for that run.
- **OpenAI (or other API):** Set `OPENAI_API_KEY`; optionally `OPENAI_BASE_URL`, `OPENAI_MODEL`.

## Environment (YouTube authentication)

YouTube increasingly requires authentication for subtitle downloads. Run `yt-artist doctor` to check your setup.

### PO Token (proof of origin) — automatic

YouTube uses PO tokens to verify requests come from a real browser. Without one, subtitle downloads will fail.

**yt-artist auto-installs a PO token provider** ([yt-dlp-get-pot-rustypipe](https://pypi.org/project/yt-dlp-get-pot-rustypipe/)) that generates tokens automatically at runtime. No manual token setup is needed.

Run `yt-artist doctor` to confirm the provider is detected. If it's missing:
```bash
pip install yt-dlp-get-pot-rustypipe
```

### Manual PO token (fallback)

If the auto-provider doesn't work for your setup, you can set a manual token via **`YT_ARTIST_PO_TOKEN`**. Get one by following the [yt-dlp PO Token guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide).

For **subtitle downloads**, the token value is typically in the format `web.subs+<token>`. To get it: open YouTube in your browser → DevTools (F12) → Network tab → filter for `v1/player` → request payload under `serviceIntegrityDimensions.poToken`.

```bash
export YT_ARTIST_PO_TOKEN=web.subs+<token_from_browser>
yt-artist doctor   # verify it's detected
```

### Cookies (for restricted content or as PO token fallback)

Cookies authenticate you as a logged-in YouTube user. They are needed for age-restricted or members-only videos, and can also serve as a fallback if the automatic PO token provider doesn't work.

| Method | Env var | What you need |
|--------|---------|---------------|
| From browser | `YT_ARTIST_COOKIES_BROWSER=chrome` | Nothing extra; just be logged into YouTube in that browser |
| Manual (file) | `YT_ARTIST_COOKIES_FILE=/path/to/cookies.txt` | Netscape-format cookies.txt (export from browser or via yt-dlp) |

Set **one** of these (browser wins if both are set):

- **`YT_ARTIST_COOKIES_BROWSER`** — Browser name (`chrome`, `firefox`, or `safari`). Uses `yt-dlp --cookies-from-browser`. You must be logged into YouTube in that browser.
- **`YT_ARTIST_COOKIES_FILE`** — Path to a Netscape-format cookies file. Uses `yt-dlp --cookies`.

Example:
```bash
export YT_ARTIST_COOKIES_BROWSER=chrome
yt-artist transcribe --artist-id @channel
```

To confirm cookies are detected, run `yt-artist doctor` — you should see `Cookies: using browser 'chrome'` or `Cookies: using file '/path/to/cookies.txt'`.

**Security note:** Using cookies ties YouTube traffic to your Google account. Prefer a secondary/throwaway account when using cookies with yt-artist/yt-dlp.

Cookies and PO token can be used together (they serve different purposes: cookies = session auth, PO token = proof of origin for bot detection). With the auto-provider installed (default), cookies are optional and only needed for restricted content or as a fallback.

## Environment (rate limits & performance)

- **`YT_ARTIST_MAX_CONCURRENCY`** — Max parallel workers for bulk operations (default: 2). Higher values are faster but risk YouTube rate limits.
- **`YT_ARTIST_INTER_VIDEO_DELAY`** — Seconds to wait between videos in bulk operations (default: 2.0).
- **`YT_ARTIST_SLEEP_REQUESTS`** — yt-dlp `--sleep-requests` value in seconds (default: 1.5).
- **`YT_ARTIST_SLEEP_SUBTITLES`** — yt-dlp `--sleep-subtitles` value in seconds (default: 2).

---

## Troubleshooting

| Issue | What to do |
|-------|------------|
| `yt-artist: command not found` | Add `~/.local/bin` to your PATH, or use `./scripts/yt-artist.sh` from the repo. |
| Summarize says “Set a default prompt or pass --prompt” | Run `yt-artist set-default-prompt --artist-id @X --prompt short` or pass `--prompt short` to summarize. |
| Summarize fails (401 / connection) | For Ollama: start it and run `ollama run mistral`. To force Ollama when an API key is set: `OPENAI_BASE_URL=http://localhost:11434/v1 yt-artist ... summarize ...` |
| build-artist-prompt returns generic “about” | Install `duckduckgo-search` for web search: `pip install yt-artist[search]`. |
| No transcript / transcribe fails | Run `yt-artist doctor`. If the PO token provider is missing: `pip install yt-dlp-get-pot-rustypipe`. If automatic doesn't work, try browser cookies: `export YT_ARTIST_COOKIES_BROWSER=chrome` (must be logged into YouTube). Or set a manual token: `export YT_ARTIST_PO_TOKEN=web.subs+<token>`. See [PO Token guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide). |
| "Sign in to confirm your age" | Video is age-restricted. Set `YT_ARTIST_COOKIES_BROWSER=chrome` and ensure you're logged into YouTube in that browser. |
| "403 Forbidden" or "confirm you're not a bot" | YouTube is blocking automated access. Ensure the PO token provider is installed (`pip install yt-dlp-get-pot-rustypipe`) or set `YT_ARTIST_PO_TOKEN`. Run `yt-artist doctor` to verify. |
| "Check your setup" | Run `yt-artist doctor` to see which components need configuration. |
| Background job shows "failed" | The process died (OOM, crash). Check the log with `yt-artist jobs attach <id>`. Stale jobs are auto-detected when you run `yt-artist jobs`. |
| Too many hints in output | Use `--quiet` or `-q` to suppress all hints and tips. |
| Want to start fresh? | Run `yt-artist quickstart` for a guided walkthrough. |

---

## One-line install (from repo clone)

```bash
./scripts/install-mac.sh && source ~/.zshrc && yt-artist --help
```

Add `export PATH="$HOME/.local/bin:$PATH"` to `~/.zshrc` or `~/.bash_profile` if the script added it for you.
