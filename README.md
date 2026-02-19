# yt-artist

Fetch YouTube channel video URLs, transcribe videos, and generate AI-powered summaries from transcripts.

- **Fetch/urllist:** Given a channel URL, writes urllist markdown and stores artists/videos in SQLite.
- **Transcribe:** One video (by URL or id) or bulk per artist (with optional auto-fetch of urllist). Parallel execution with configurable concurrency.
- **Summarize:** One video or bulk per artist; uses per-artist default prompt or `--prompt`. Auto-creates artist/video/transcript when missing. Parallel execution. Long transcripts handled via map-reduce or refine strategies (`--strategy`).
- **Quality scoring:** Automated heuristic + LLM self-check scoring with hallucination guardrails. Named entity verification catches fabricated names. Faithfulness tracked separately. Optional `--verify` for deep claim verification. Standalone `yt-artist score` command.
- **Export/backup:** Portable JSON (chunked per-artist, self-contained) or flat CSV export. `--zip` for email-friendly compressed files. Never locks your data in SQLite.
- **Background jobs:** Push long-running bulk operations to the background with `--bg`. Monitor with `yt-artist jobs`, attach to logs, stop running jobs.
- **Per-artist default prompt** and optional **build-artist-prompt** (search + "about" text).
- **Guided onboarding:** Next-step hints after every command, `quickstart` walkthrough, `--quiet` for scripting.
- **Rate-limit safe:** Conservative defaults for YouTube API calls with configurable delays and cookie support.

Data model supports future: video stats, most-replayed segments, transcript-based screenshots.

**📖 [User Guide (USER_GUIDE.md)](USER_GUIDE.md)** — Install on Mac, command reference, quick start, troubleshooting.

## Portable install (Mac) — terminal only

Setup is entirely in the terminal. One script creates the venv, installs yt-artist and dependencies (including yt-dlp), puts `yt-artist` in `~/.local/bin`, adds that to your PATH in shell config if needed, and runs `yt-artist --help` to verify.

```bash
git clone <repo-url> yt-artist && cd yt-artist
./scripts/install-mac.sh
```

If the script added PATH to your config, in new terminals run `source ~/.zshrc` (or open a new tab). Then: `yt-artist --help`

**If nothing shows in the terminal or the command isn’t found**, run from the repo instead:  
`./scripts/yt-artist.sh --help` — see [USER_GUIDE.md](USER_GUIDE.md) “Not working in Mac terminal?”.

Then run `yt-artist doctor` to verify yt-dlp and PO token (needed for transcribe).

See [USER_GUIDE.md](USER_GUIDE.md) for quick start and command reference.

## Install (Linux / manual)

On Linux (or Mac without the install script), use the developer setup:

```bash
git clone <repo-url> yt-artist && cd yt-artist
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

Then run with: `.venv/bin/yt-artist --help`

Run `yt-artist doctor` to verify yt-dlp and PO token.

For AI summaries, install [Ollama](https://ollama.com) and pull a model: `ollama run mistral`.

## Setup (developers)

```bash
python3 -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install --upgrade pip setuptools wheel   # requires pip >= 22 for editable pyproject.toml installs
pip install -e ".[dev]"
```

Ensure **yt-dlp** is on your PATH (e.g. `pip install yt-dlp` in the same venv). Transcribe needs the PO token provider (installed with the package). Run `yt-artist doctor` to verify. For **summarize**, install `openai` (`pip install openai`). **Local Ollama is used by default** when `OPENAI_API_KEY` is not set; have Ollama running and a model pulled (e.g. `ollama run mistral`).

## Usage

```bash
# Urllist for a channel (or use alias: urllist)
yt-artist fetch-channel "https://www.youtube.com/@channel"

# Add a prompt and set per-artist default
yt-artist add-prompt --id short --name "Short summary" \\
  --template "Summarize in 2-3 sentences for {audience}. Artist: {artist}. Video: {video}." \\
  --audience-component "general"
yt-artist set-default-prompt --artist-id @channel --prompt short

# Transcribe one video or all videos for an artist (bulk)
yt-artist transcribe "https://www.youtube.com/watch?v=VIDEO_ID"
yt-artist transcribe --artist-id @channel

# Summarize one video or all for an artist (prompt: --prompt else artist default)
yt-artist summarize "https://youtube.com/watch?v=VIDEO_ID"
yt-artist summarize --artist-id @channel
yt-artist summarize --artist-id @channel --strategy refine  # max coherence for long transcripts

# Score summaries for quality (heuristic + LLM self-check)
yt-artist score --artist-id @channel
yt-artist score --artist-id @channel --verify  # deep claim verification (1 extra LLM call)

# Background: push long-running bulk ops to background
yt-artist --bg transcribe --artist-id @channel
yt-artist jobs                    # list all jobs with progress
yt-artist jobs attach <job_id>    # tail the log (Ctrl-C to detach)
yt-artist jobs stop <job_id>      # stop a running job
yt-artist jobs clean              # remove old finished jobs

# Export data for backup (JSON or CSV)
yt-artist export --artist-id @channel
yt-artist export --format csv --zip             # compressed CSV tables

# Optional: build "about" from web search and set as default prompt
yt-artist build-artist-prompt --artist-id @channel --save-as-default
yt-artist list-prompts
yt-artist search-transcripts [--artist-id ID] [--video-id ID]

# Check your setup (yt-dlp, auth, LLM):
yt-artist doctor

# First time? Get a guided walkthrough:
yt-artist quickstart
```

## Config

- **DB path:** `--db path/to/yt_artist.db` (default: `<data-dir>/data/yt_artist.db`)
- **Data dir:** `--data-dir path` (default: current working directory)
- **Per-artist default prompt:** Set with `set-default-prompt`; fallback: env `YT_ARTIST_DEFAULT_PROMPT` or first prompt in DB.
- **YouTube authentication (for transcription):** A PO token provider (`yt-dlp-get-pot-rustypipe`) is auto-installed to handle YouTube's bot detection automatically — no manual token setup needed.
  - `YT_ARTIST_PO_TOKEN` — Manual fallback: proof-of-origin token. See [PO Token guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide).
  - `YT_ARTIST_COOKIES_BROWSER` — Browser name for `yt-dlp --cookies-from-browser` (e.g. `chrome`, `firefox`, `safari`). Required for age-restricted/members-only videos. **Strongly recommended for bulk transcription (50+ videos)** — authenticated requests get much higher YouTube rate limits. See [USER_GUIDE.md](USER_GUIDE.md) "Bulk transcription and rate limits".
  - `YT_ARTIST_COOKIES_FILE` — Path to a Netscape-format cookies file (alternative to browser cookies).
  - Run `yt-artist doctor` to check your authentication setup.
- **LLM (for summarize):** Uses **local Ollama by default** when `OPENAI_API_KEY` is not set.
  - `OPENAI_BASE_URL` — API base URL (default: `http://localhost:11434/v1` for Ollama; or `https://api.openai.com/v1` when API key is set)
  - `OPENAI_API_KEY` — If set, use OpenAI (or another provider); if unset, use Ollama with key `ollama`
  - `OPENAI_MODEL` — Model name (default: `mistral` for Ollama; `gpt-4o-mini` for OpenAI). For Ollama use e.g. `mistral`, `llama3.2`, `llama3`

## MCP Server (optional)

Install with MCP support: `pip install -e ".[mcp]"`

Run the MCP server (stdio transport):

```bash
yt-artist-mcp
```

Or with env (optional): `YT_ARTIST_DB`, `YT_ARTIST_DATA_DIR`.

**Add to Cursor MCP config** (e.g. `~/.cursor/mcp.json` or project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "yt-artist": {
      "command": "yt-artist-mcp",
      "env": {}
    }
  }
}
```

If the CLI is installed in a venv, use the full path to the venv’s `yt-artist-mcp` (e.g. `"/path/to/project/.venv/bin/yt-artist-mcp"`).

**Tools:** `fetch_channel`, `transcribe_video`, `summarize_video`, `list_artists`, `list_videos`.

## Quick real-URL test

From the project root (with venv active and `yt-dlp` installed):

```bash
export PYTHONPATH=src   # if not installed via pip

# 1. Add a prompt
yt-artist --db test_run.db add-prompt --id short --name "Short summary" \
  --template "Summarize in 2-3 sentences for {audience}. Artist: {artist}. Video: {video}." \
  --audience-component "general"
yt-artist --db test_run.db list-prompts

# 2. Fetch a channel (use any YouTube channel URL)
yt-artist --db test_run.db --data-dir . fetch-channel "https://www.youtube.com/@SomeChannel"

# 3. Verify setup (yt-dlp, PO token, LLM)
yt-artist doctor

# 4. Transcribe one video (use a video ID from the urllist or any YouTube video URL)
yt-artist --db test_run.db transcribe "https://www.youtube.com/watch?v=VIDEO_ID"

# 5. Summarize one video or all for artist (uses local Ollama by default; run `ollama run mistral` first)
yt-artist --db test_run.db summarize VIDEO_ID
yt-artist --db test_run.db summarize --artist-id @SomeChannel
```

## Docs

- [Architecture decisions](docs/adr/00-INDEX.md) — ADR index (14 ADRs)
- [Architecture diagrams](docs/ARCHITECTURE_DIAGRAMS.md) — module graph, data flow, ER diagram, pipeline, strategies, scoring, BAML prompts, hallucination guardrails
- [Development journey](docs/JOURNEY.md) — how this project was built iteratively
- [Session summary](docs/SESSION_SUMMARY.md) — detailed technical log of all development sessions
- [Parking lot](docs/PARKING_LOT.md) — prioritized future work with suggestion flags
