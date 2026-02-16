# yt-artist

Python 3.9+ CLI: fetch YouTube channel videos, transcribe via yt-dlp, summarize via OpenAI-compatible LLM (Ollama default).

## Stack

- Python 3.9+, setuptools, pyproject.toml
- SQLite (WAL mode, FK enforcement) — schema in src/yt_artist/schema.sql
- yt-dlp subprocess calls for YouTube data
- openai SDK for LLM (local Ollama or remote OpenAI)
- pytest + pytest-cov for testing
- ruff for formatting + linting

## Layout

- src/yt_artist/ — package source (cli.py is entrypoint, storage.py is DB layer)
- tests/ — pytest tests (conftest.py has db_path and store fixtures)
- scripts/ — install and wrapper scripts
- docs/ — ADRs, plans, journey, parking lot
- data/ — runtime data dir (gitignored)

## Commands

```
python -m pytest tests/ -v                              # all tests
python -m pytest tests/test_storage.py -v               # single file
python -m pytest tests/ -v --cov=yt_artist              # with coverage
pip install -e ".[dev]"                                  # install dev deps
python -m yt_artist.cli --help                           # CLI help
ruff format src/ tests/                                  # format
ruff check src/ tests/ --fix                             # lint + autofix
```

## Key Patterns

- Storage layer uses dict row factory — all DB rows are dicts not tuples
- TypedDict row types in storage.py (ArtistRow, VideoRow, etc.)
- CLI: argparse subcommands, each command is _cmd_* function taking (args, storage, data_dir)
- Upsert pattern everywhere: INSERT ON CONFLICT DO UPDATE
- Background jobs: re-exec as subprocess with --_bg-worker flag
- Concurrency via ThreadPoolExecutor, capped at MAX_CONCURRENCY=3
- Pipeline parallelism: producer-consumer with DB-polling in pipeline.py (ADR-0012)
- Long-transcript strategies: auto/truncate/map-reduce/refine in summarizer.py (ADR-0013)
- Quality scoring: heuristic + LLM self-check in scorer.py, decoupled 3rd pipeline stage
- Rate-limit tracking: request_log table, check_rate_warning() in rate_limit.py
- Tests mock yt-dlp and LLM calls — never hit real YouTube in tests

## Conventions

- Brief communication. Sacrifice grammar not clarity.
- snake_case functions. PascalCase classes. UPPER_SNAKE constants.
- Private functions prefixed with underscore.
- Docstrings on public functions. Module docstring on every file.
- `from __future__ import annotations` on files using `X | Y` union syntax.
- Logging: `log = logging.getLogger("yt_artist.<module>")`
- Errors: SystemExit for user-facing, ValueError/RuntimeError for internal.
- Parameterized SQL queries (? placeholders) — never string interpolation.

## DB Schema (tables)

artists, videos, transcripts, prompts, summaries, jobs, request_log, screenshots (future), video_stats (future)

## Environment Variables

```
YT_ARTIST_DB                    # database path
YT_ARTIST_DATA_DIR              # data directory
YT_ARTIST_DEFAULT_PROMPT        # default prompt ID
YT_ARTIST_PO_TOKEN              # YouTube PO token
YT_ARTIST_COOKIES_BROWSER       # browser for cookie extraction
YT_ARTIST_COOKIES_FILE          # Netscape cookies file
OPENAI_API_KEY                  # triggers OpenAI instead of Ollama
OPENAI_BASE_URL                 # LLM endpoint (default: localhost:11434/v1)
OPENAI_MODEL                    # LLM model name
YT_ARTIST_MAX_TRANSCRIPT_CHARS  # max chars sent to LLM (default: 30000)
YT_ARTIST_SUMMARIZE_STRATEGY    # auto|truncate|map-reduce|refine (default: auto)
```

## Worktree / Parallel Work

Use git worktree for parallel feature branches. Each worktree gets its own data/ and *.db.
Never share SQLite files between worktrees.

## Important

- NEVER commit cookies.txt, .env, or *.db files
- NEVER call real YouTube APIs in tests — always mock yt-dlp subprocess calls
- DB files are gitignored. Tests use tmp_path fixtures.
- Error messages must be actionable — this project targets non-technical users
