# ADR-0015: FTS5 full-text transcript search

## Status

Accepted (2026-02-19). Implemented in Session 22.

## Context

`search-transcripts` only supported exact video_id/artist_id filtering — no text search. Users with 500+ transcribed videos couldn't search *within* transcript content (e.g., "find all videos mentioning dopamine").

### Alternatives considered

| Option | Verdict | Reason |
|--------|---------|--------|
| `LIKE '%term%'` | Rejected | No ranking, no snippets, no phrase search. Full table scan on every query. |
| FTS5 (built-in) | **Accepted** | BM25 ranking, snippet extraction, phrase/prefix/boolean queries. Zero dependencies — ships with SQLite. |
| Hybrid FTS5 + vector embeddings | Rejected | Transcripts are verbatim speech — keyword search matches user intent well. Vector search adds ~60K chunks to embed, ~100 min indexing, sqlite-vec C extension distribution burden. ADR-0013 previously rejected this direction. |

## Decision

### FTS5 external content table

Use `content=transcripts, content_rowid=rowid` — the FTS index references the existing `transcripts` table rather than storing text twice. SQLite reads from the source table on demand for snippet extraction.

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    raw_text,
    content=transcripts,
    content_rowid=rowid
);
```

`content_rowid=rowid` works because SQLite assigns an implicit integer rowid even with TEXT PRIMARY KEY (the table is not WITHOUT ROWID).

### Sync triggers

Three triggers keep the FTS index in sync:

- `transcripts_ai` — AFTER INSERT: adds new transcript to index
- `transcripts_ad` — AFTER DELETE: removes from index
- `transcripts_au` — AFTER UPDATE: removes old, adds new (handles ON CONFLICT DO UPDATE upserts)

### Migration-only creation

FTS5 virtual table and triggers are created **only** in `_migrate_fts5_transcripts()` in storage.py, **not** in schema.sql. This is because:

1. `executescript()` runs schema.sql before migrations — if FTS5 were in schema.sql, the migration's `already_exists` check would always be True, preventing the `rebuild` command from indexing existing transcripts.
2. The migration probes FTS5 availability (some SQLite builds lack it) and gracefully degrades.
3. On first creation, `INSERT INTO transcripts_fts(transcripts_fts) VALUES('rebuild')` indexes all existing transcripts.

### Query syntax pass-through

Users get full FTS5 query syntax: phrase search (`"quoted"`), prefix (`word*`), boolean (`OR`), implicit AND. Syntax errors are caught as `OperationalError` and re-raised as `ValueError` with a usage hint.

### Snippet extraction

`snippet(transcripts_fts, 0, '[', ']', '...', 32)` extracts ~32 tokens of context around matches. `[` `]` markers are terminal-safe (no encoding issues).

### Dual-mode CLI

- `search-transcripts` without `--query`: existing list behavior (backward compatible)
- `search-transcripts --query "dopamine"`: FTS5 search with ranked results and snippets

## Consequences

**Positive:**
- Sub-millisecond full-text search across all transcripts
- BM25 ranking surfaces most relevant videos first
- Snippet context shows where matches occur without reading full transcript
- Zero new dependencies — FTS5 is built into SQLite
- No duplicate storage — external content table references existing transcripts
- Triggers handle sync automatically, including upserts

**Negative:**
- FTS5 not available in all SQLite builds (migration gracefully degrades, doctor reports status)
- External content tables require manual rebuild if triggers are bypassed (raw SQL outside the app)
- FTS5 query syntax errors produce cryptic messages (mitigated by catching and re-raising with usage hint)

**Files:**
- `src/yt_artist/storage.py` — `_migrate_fts5_transcripts()`, `search_transcripts()`, `has_fts5()`
- `src/yt_artist/cli.py` — `--query`/`-q`, `--limit` args, dual-mode handler, doctor [7/7] FTS5 check
- `src/yt_artist/mcp_server.py` — `search_transcripts` MCP tool
- `tests/test_fts_search.py` — 26 tests (storage, migration, CLI)
