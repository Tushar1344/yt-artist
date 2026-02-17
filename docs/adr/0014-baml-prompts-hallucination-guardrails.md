# ADR-0014: BAML prompt management and hallucination guardrails

## Status

Accepted (2026-02-17). Implemented in Sessions 11–12.

## Context

Two problems driving this change:

1. **Hallucinations** — The Hubermanlab willpower episode (`cwakOgHIT0E`, 132K chars) hallucinated "Elijah Wood" as the speaker. Root causes: no faithfulness instructions in prompts, blind LLM self-check (sees 2% of transcript via `transcript[:3000]`), no entity verification, non-actionable scores.

2. **Prompt management** — All prompts were hardcoded Python string constants scattered across `summarizer.py` and `scorer.py`. No versioning, no benchmarking, no structured inputs/outputs. Changing a prompt required editing Python source.

## Decision

### BAML for prompt management

Migrate all LLM prompts to [BAML](https://github.com/BoundaryML/baml) — typed prompt functions in `.baml` files, git-versioned, Ollama-compatible, with structured input/output types.

**Architecture:**

```
baml_src/*.baml          → Prompt definitions (source of truth)
    ↓ baml-cli generate
baml_client/             → Auto-generated typed Python (gitignored)
    ↓
src/yt_artist/prompts.py → Thin adapter (6 functions wrapping baml_client.b)
    ↓
summarizer.py, scorer.py → Call prompts.* instead of llm.complete()
```

**Files:**
- `baml_src/clients.baml` — Ollama + OpenAI client configs
- `baml_src/summarize.baml` — 4 functions: SummarizeSinglePass, SummarizeChunk, ReduceChunkSummaries, RefineSummary
- `baml_src/score.baml` — 2 functions: ScoreSummary (→ typed ScoreRating), VerifyClaims (→ ClaimVerification[])
- `src/yt_artist/prompts.py` — Adapter bridging BAML → codebase

### Hallucination guardrails (3 tiers)

**Tier 1 — Prompt hardening (0 extra LLM calls):**
Every BAML prompt includes "Do not invent" / "Only state facts" / "Only include information explicitly stated" instructions. Checked by `tests/test_prompts.py` which reads `.baml` files and asserts anti-hallucination language.

**Tier 2 — Improved scoring (0 extra LLM calls):**
- `_named_entity_score()`: Regex-extract proper nouns from summary, check each against transcript. "Elijah Wood" not in transcript → score ≈ 0.0.
- `_sample_transcript()`: Stratified sampling (start/middle/end) replaces blind `transcript[:3000]`. LLM sees representative 3K chars from all sections.
- Faithfulness tracked separately: `faithfulness_score REAL` column in DB. Low faithfulness (≤ 0.4) triggers `log.warning()` and `[!LOW FAITHFULNESS]` CLI marker.
- Rebalanced heuristic weights: `0.25*length + 0.15*repetition + 0.25*coverage + 0.15*structure + 0.20*entity`

**Tier 3 — Claim verification (1 extra LLM call, opt-in):**
- `VerifyClaims` BAML function: LLM lists 5 claims from summary, marks each VERIFIED/UNVERIFIED against transcript.
- `--verify` flag on `score` CLI command.
- `verification_score REAL` column in DB.

### LLM call cost

| Tier | Extra calls per summary | When |
|------|------------------------|------|
| 1 | 0 | Always (prompt changes in .baml files) |
| 2 | 0 | Always (heuristic + better excerpt) |
| 3 | 1 | Only with `--verify` flag |

## Consequences

**Positive:**
- Prompts are git-versioned `.baml` files with full diff history
- Typed outputs (ScoreRating, ClaimVerification) eliminate parsing bugs
- Named entity verification catches hallucinated proper nouns without any LLM call
- Stratified transcript sampling gives LLM representative context
- Faithfulness tracked separately — actionable signal for hallucination detection
- Claim verification available on-demand for high-stakes summaries

**Negative:**
- New `baml-py` dependency (~50MB)
- `baml-cli generate` step in dev workflow
- `baml_client/` is auto-generated (gitignored), must regenerate after `.baml` changes

**Migration:**
- `llm.complete()` still exists for `artist_prompt.py` but no longer used by summarizer/scorer
- Existing `quality_score`/`heuristic_score`/`llm_score` columns unchanged
- New columns (`faithfulness_score`, `verification_score`) added via schema migration, default NULL
