# ADR-0004: CLI first, MCP optional

**Status:** Accepted  
**Date:** 2026-02-08  
**Deciders:** Implementation + plan

## Context

User wants the system usable as CLI, MCP server, or Cursor skill. Need a clear order of implementation and a single core to share.

## Decision

Implement **CLI first** as the primary interface; core library is used by CLI. **MCP server** is optional and calls the same core. **Cursor skill** is documentation that invokes CLI or MCP.

## Consequences

- Positive: One code path; CLI is testable and scriptable; MCP adds tools without duplicating logic.
- Negative: MCP adds dependency (e.g. mcp package) when implemented.
- Follow-ups: Document MCP config for Cursor in README.

## Links

- Plan: section 2 (Architecture), section 7 (Tasks E, F, G)
- Scratch: docs/scratch/SCRATCH.md#5-cli
