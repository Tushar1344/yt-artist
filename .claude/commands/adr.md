Create a new Architecture Decision Record: $ARGUMENTS

**Step 1 — Determine next number**: Read docs/adr/00-INDEX.md, find the highest ADR number, increment by 1. Pad to 4 digits (e.g. 0012).

**Step 2 — Create the ADR file**: `docs/adr/<number>-<slug>.md`

Use this exact format (matches existing ADRs):
```markdown
# ADR-<number>: <title>

## Status

Accepted (<today's date>).

## Context

<Why this decision is needed. 2-3 sentences.>

## Decision

<What we decided. Be specific. Include code patterns or config if relevant.>

## Alternatives Considered

| Alternative | Why not chosen |
|-------------|---------------|
| ... | ... |

## Consequences

<What changes as a result. Bullet points.>
```

**Step 3 — Update the index**: Add the new ADR to `docs/adr/00-INDEX.md` under the appropriate category. Follow the existing tree structure.

**Step 4 — Verify**: Confirm the file exists and the index links to it correctly.
