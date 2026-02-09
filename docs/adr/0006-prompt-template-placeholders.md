# ADR-0006: Prompt template with artist/video/intent/audience

**Status:** Accepted  
**Date:** 2026-02-08  
**Deciders:** Implementation + plan

## Context

Summaries are per video and per “prompt”; user wants prompt to have artist, video, intent, and audience components.

## Decision

**Prompt** table stores a template string with placeholders `{artist}`, `{video}`, `{intent}`, `{audience}`. Optional columns for component hints (e.g. intent_component, audience_component). Summarizer fills placeholders from DB + CLI/MCP overrides, then sends to LLM with transcript.

## Consequences

- Positive: Multiple summaries per video (different prompts); reusable prompts; overrides without new prompt row.
- Negative: Template injection if user-controlled; we sanitize or restrict to known keys.
- Follow-ups: Document how to add new prompts (DB insert or future CLI).

## Links

- Plan: section 1 (Data model), section 4 (Summarizer)
- Scratch: docs/scratch/SCRATCH.md#4-summarizer
