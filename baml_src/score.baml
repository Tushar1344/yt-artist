// Quality scoring prompt functions for yt-artist.
// Two functions: LLM self-check (always) and claim verification (opt-in).

class ScoreRating {
  completeness int @description("1-5: Does it cover the main topics of the transcript?")
  coherence int @description("1-5: Does it read naturally and logically?")
  faithfulness int @description("1-5: 1 if it contains names or claims not in the transcript, 5 if everything is verifiable")
}

// LLM self-check — rate a summary against a transcript excerpt.
function ScoreSummary(transcript_excerpt: string, summary: string) -> ScoreRating {
  client Ollama
  prompt #"
    Rate this summary of a video transcript on a scale of 1-5 for each criterion:
    - Completeness: Does it cover the main topics of the transcript?
    - Coherence: Does it read naturally and logically?
    - Faithfulness: Does it ONLY state facts, names, and quotes found in the transcript?
      Give 1 if it contains names or claims not in the transcript, 5 if everything is verifiable.

    Transcript excerpt:
    {{ transcript_excerpt }}

    Summary:
    {{ summary }}

    {{ ctx.output_format }}
  "#
}

class ClaimVerification {
  claim string @description("A specific factual claim from the summary")
  verified bool @description("true if the claim appears in the transcript, false if not found")
}

// Claim verification — fact-check specific claims against transcript.
// Used with --verify flag (opt-in, 1 extra LLM call).
function VerifyClaims(summary: string, transcript_excerpt: string) -> ClaimVerification[] {
  client Ollama
  prompt #"
    You are a fact-checker. Given a summary and transcript excerpts, verify the summary's accuracy.

    List exactly 5 specific factual claims from the summary (names, numbers, quotes, events).
    For each claim, state whether it is verified (appears in the transcript) or not.

    Summary:
    {{ summary }}

    Transcript excerpts:
    {{ transcript_excerpt }}

    {{ ctx.output_format }}
  "#
}
