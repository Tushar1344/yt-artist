# yt-artist

Python 3.9+ CLI: fetch YouTube channel videos, transcribe via yt-dlp, summarize via OpenAI-compatible LLM (Ollama default).

## Stack

- Python 3.9+, setuptools, pyproject.toml
- SQLite (WAL mode, FK enforcement) — schema in src/yt_artist/schema.sql
- yt-dlp subprocess calls for YouTube data
- openai SDK for LLM (local Ollama or remote OpenAI)
- baml-py for typed LLM prompt functions (.baml files)
- pytest + pytest-cov for testing
- ruff for formatting + linting

## Layout

- src/yt_artist/ — package source (cli.py is entrypoint, storage.py is DB layer, exporter.py is export/backup)
- baml_src/ — BAML prompt definitions (.baml files, git-versioned)
- tests/ — pytest tests (conftest.py has db_path and store fixtures)
- scripts/ — install, wrapper, and monitoring scripts (monitor.sh = live dashboard)
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
./scripts/monitor.sh                                     # live dashboard (per-artist progress, jobs, processes, rate limits)
./scripts/monitor.sh 10                                  # dashboard with 10s refresh
```

## Key Patterns

- Storage layer uses dict row factory — all DB rows are dicts not tuples
- TypedDict row types in storage.py (ArtistRow, VideoRow, etc.)
- CLI: argparse subcommands, each command is _cmd_* function taking (args, storage, data_dir)
- Upsert pattern everywhere: INSERT ON CONFLICT DO UPDATE
- Background jobs: re-exec as subprocess with --_bg-worker flag
- Concurrency via ThreadPoolExecutor, capped at ConcurrencyConfig.max_concurrency=3 (bulk transcribe/summarize/score)
- Parallel map-reduce: chunk summaries run concurrently in map phase (ConcurrencyConfig.map_concurrency workers)
- Pipeline parallelism: producer-consumer with DB-polling in pipeline.py (ADR-0012)
- Long-transcript strategies: auto/truncate/map-reduce/refine in summarizer.py (ADR-0013)
- Quality scoring: heuristic + LLM self-check in scorer.py, decoupled 3rd pipeline stage
- BAML prompts: scoring/verification only (.baml files → baml_client/ → prompts.py adapter). Summarization uses DB-stored templates rendered via _fill_template() in summarizer.py.
- Hallucination guardrails: entity verification, faithfulness tracking, --verify claim check in scorer.py
- IN-query batching: _execute_chunked_in() splits large WHERE IN clauses into _IN_BATCH_SIZE (500) chunks to stay under SQLite's 999 param limit
- Connection context managers: _read_conn() for reads, _write_conn() for single writes, transaction() for batch writes
- Path centralization: paths.py has pure functions for all runtime data file paths (no mkdir)
- Config centralization: config.py has typed frozen dataclasses for all env vars, @lru_cache accessors. Tests clear caches via conftest autouse fixture.
- JSON output: `--json` global flag on CLI, `_json_print()` helper. Supported by: list-prompts, search-transcripts, status, jobs list, doctor, set-about, export
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
YT_ARTIST_DEFAULT_PROMPT        # default prompt ID (default: "default")
YT_ARTIST_LOG_LEVEL             # logging level (default: INFO)
YT_ARTIST_PO_TOKEN              # YouTube PO token
YT_ARTIST_COOKIES_BROWSER       # browser for cookie extraction
YT_ARTIST_COOKIES_FILE          # Netscape cookies file
YT_ARTIST_INTER_VIDEO_DELAY     # seconds between bulk yt-dlp calls (default: 2.0)
YT_ARTIST_SLEEP_REQUESTS        # yt-dlp --sleep-requests value (default: "1")
YT_ARTIST_SLEEP_SUBTITLES       # yt-dlp --sleep-subtitles value (default: "3")
OPENAI_API_KEY                  # triggers OpenAI instead of Ollama
OPENAI_BASE_URL                 # LLM endpoint (default: localhost:11434/v1)
OPENAI_MODEL                    # LLM model name
YT_ARTIST_MAX_TRANSCRIPT_CHARS  # max chars sent to LLM (default: 30000)
YT_ARTIST_SUMMARIZE_STRATEGY    # auto|truncate|map-reduce|refine (default: auto)
YT_ARTIST_MAP_CONCURRENCY       # max workers for map-reduce chunk parallelism (default: 3, set 1 to disable)
```

All env vars are centralized in `config.py` via frozen dataclasses (`YouTubeConfig`, `LLMConfig`, `AppConfig`, `ConcurrencyConfig`) with `@lru_cache` accessor functions. Callers import from config.py — never read `os.environ` directly.

## Worktree / Parallel Work

Use git worktree for parallel feature branches. Each worktree gets its own data/ and *.db.
Never share SQLite files between worktrees.

## Important

- NEVER commit cookies.txt, .env, or *.db files
- NEVER call real YouTube APIs in tests — always mock yt-dlp subprocess calls
- DB files are gitignored. Tests use tmp_path fixtures.
- Error messages must be actionable — this project targets non-technical users
