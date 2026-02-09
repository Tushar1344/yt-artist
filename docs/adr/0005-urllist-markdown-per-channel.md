# ADR-0005: One markdown urllist per channel

**Status:** Accepted  
**Date:** 2026-02-08  
**Deciders:** Implementation + plan

## Context

User requirement: put channel video URLs into a file named like `artist{id}artistname-urllist.md`.

## Decision

One markdown file per channel: `data/artists/{artist_id}/artist{id}{sanitized_name}-urllist.md`. Content: one URL per line or markdown list, with optional title. Rest of data (videos, transcripts, summaries) in SQLite.

## Consequences

- Positive: Human-readable URL list; matches requested naming; directory per artist for future transcripts/screenshots.
- Negative: Two sources of truth (file + DB) for “which videos”; fetcher keeps them in sync.
- Follow-ups: Fetcher must be idempotent (rewrite file, upsert DB).

## Links

- Plan: section 1 (Data model, File layout)
- Scratch: docs/scratch/SCRATCH.md#2-fetcher
