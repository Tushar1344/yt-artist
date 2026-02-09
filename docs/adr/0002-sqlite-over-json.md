# ADR-0002: Use SQLite for all structured data

**Status:** Accepted  
**Date:** 2026-02-08  
**Deciders:** Implementation + plan

## Context

Structured data: artists, videos, transcripts, prompts, summaries (and future screenshots, video stats). Need queryable, single-file, extensible storage.

## Decision

Use **SQLite** for all structured data in one file (e.g. `data/yt_artist.db`). Schema in a single migration/schema file; stdlib `sqlite3` only.

## Consequences

- Positive: No server, queryable, one file to backup, easy to add tables later.
- Negative: No built-in migrations; we use one schema.sql or init script.
- Follow-ups: Add migration story if schema evolves often.

## Links

- Plan: section 1 (Data model), section 3 (Tool selection)
- Scratch: docs/scratch/SCRATCH.md#1-foundation
