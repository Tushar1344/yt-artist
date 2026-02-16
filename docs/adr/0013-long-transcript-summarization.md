# ADR-0013: Long-transcript summarization strategies and quality scoring

## Status

Accepted (2026-02-16). Implemented in Session 10.

## Context

Huberman Lab transcripts range from 30K to 160K characters. The default local model (Mistral 7B via Ollama) has an 8K token context window (~30K chars). Our truncation approach (Session 2) silently dropped up to 80% of long transcripts before summarization. Users had no way to know what was lost, and no way to assess summary quality.

Two problems needed solving:
1. **Long transcripts:** Summarize the full content, not just the first 30K chars.
2. **Quality assessment:** Know whether a summary is good without reading it.

## Decision

### Summarization strategies

Four strategies, selectable via `--strategy` flag or `YT_ARTIST_SUMMARIZE_STRATEGY` env var:

| Strategy | How it works | When to use |
|----------|-------------|-------------|
| `auto` (default) | Single-pass if fits, map-reduce if too long | General purpose |
| `truncate` | Cut to max_chars then single-pass (legacy) | Backward compat |
| `map-reduce` | Chunk → summarize each → combine → reduce | Speed, bulk ops |
| `refine` | Iteratively refine rolling summary per chunk | Max coherence |

**Why map-reduce as default for long transcripts:**
- Huberman episodes are semi-independent segments — map-reduce handles this well.
- Parallelizable (map step) — fits existing ThreadPoolExecutor infrastructure.
- Simplest to implement correctly. Recursive reduce handles edge cases.

**Why refine as an option:**
- Sequential content (narrative podcasts) benefits from rolling context.
- Strictly sequential — cannot parallelize — so not the default.

**Chunking design:**
- Split at sentence boundaries (`. `, `\n`, `? `, `! `) near target chunk size.
- Configurable overlap (default 500 chars, clamped to ≤ chunk_size/2).
- Never returns empty list. Forward progress guaranteed.

### Quality scoring (decoupled)

Scoring is a separate pipeline stage, not part of summarization. This allows:
- Scoring already-summarized videos retroactively.
- Skipping scoring on long runs (auto-skip when estimate >3h).
- Running scoring independently via `yt-artist score`.

**Two-tier scoring:**

1. **Heuristic (zero LLM cost, instant):** Weighted average of:
   - Length ratio (0.3): summary/transcript ratio in 0.02–0.10 is ideal.
   - Key-term coverage (0.3): top-20 frequent transcript words appearing in summary.
   - Repetition (0.2): duplicate sentence detection (model looping).
   - Structure (0.2): sentence count, bullet points, section headers.

2. **LLM self-check (1 extra call per summary):** Ask the same model to rate 1–5 on completeness, coherence, faithfulness. Parse "4 3 5" output. Normalize to 0.0–1.0.

**Combined score:** `quality_score = 0.4 * heuristic + 0.6 * llm_score`. Falls back to heuristic-only if LLM call fails.

### Pipeline integration (3-stage)

```
[YouTube] → transcribe → [DB:transcript] → summarize → [DB:summary] → score → [DB:quality_score]
```

Stage 3 runs after summarize pool completes. Single worker (scoring calls are tiny ~100 tokens). Polls for summarized-but-unscored videos.

## Alternatives Considered

| Alternative | Why not chosen |
|-------------|---------------|
| Hierarchical (multi-level) | Overkill for podcast episodes. Map-reduce with recursive reduce is sufficient. |
| Semantic chunking (embedding-based) | Requires embedding model dependency. Diminishing returns for conversational transcripts. |
| ROUGE/BERTScore | Requires reference summaries we don't have. |
| GPT-4-as-judge | Users run local Ollama — no stronger judge model available. |
| Embedding similarity | Adds sentence-transformer dependency. Too heavy for a CLI tool. |

## Consequences

- Long transcripts are fully summarized instead of silently truncated.
- Default behavior changes from truncation to auto (map-reduce). Existing `strategy="truncate"` preserves legacy behavior.
- More LLM calls per long video (map-reduce: N chunks + 1 reduce). Acceptable since LLM is local and fast.
- Quality scores enable filtering low-quality summaries without reading them.
- Schema adds 3 nullable columns to `summaries` table — backward compatible.
- 53 new tests. 378 total tests passing.
