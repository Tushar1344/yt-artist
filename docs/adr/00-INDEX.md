# Architecture Decision Log — Index (Tree)

- **Storage**
  - [ADR-0002](0002-sqlite-over-json.md) — Use SQLite for all structured data
  - [ADR-0005](0005-urllist-markdown-per-channel.md) — One markdown urllist per channel
- **Tooling**
  - [ADR-0001](0001-use-yt-dlp.md) — Use yt-dlp for channel listing and transcripts
  - [ADR-0003](0003-openai-compatible-llm.md) — OpenAI-compatible client for summaries
- **Interfaces**
  - [ADR-0004](0004-cli-first-mcp-optional.md) — CLI first, MCP optional
- **Data model**
  - [ADR-0006](0006-prompt-template-placeholders.md) — Prompt template with artist/video/intent/audience
  - [ADR-0007](0007-cli-bulk-and-per-artist-defaults.md) — CLI bulk ops, per-artist default prompt, auto-dependencies
- **Performance & reliability**
  - [ADR-0008](0008-background-jobs.md) — Background jobs for long-running bulk operations
  - [ADR-0010](0010-rate-limit-safety.md) — YouTube rate-limit safety (delays, backoff, cookies)
  - [ADR-0011](0011-parallel-execution.md) — Parallel execution with ThreadPoolExecutor
  - [ADR-0012](0012-pipeline-parallelism.md) — Pipeline parallelism for bulk transcribe + summarize (proposed)
- **User experience**
  - [ADR-0009](0009-guided-onboarding.md) — Guided onboarding (hints, quickstart, --quiet)
