// Summarization prompt functions for yt-artist.
// Each function maps to a stage of the summarization pipeline.
// All prompts include explicit faithfulness instructions.

// Single-pass summarization — used when transcript fits context window.
function SummarizeSinglePass(transcript: string, artist: string, video_title: string) -> string {
  client Ollama
  prompt #"
    You are a helpful assistant that summarizes YouTube video transcripts.
    Artist/channel context: {{ artist }}
    Video title: {{ video_title }}

    Provide a clear, concise summary of the key points discussed in the transcript.

    IMPORTANT: Only state facts, names, quotes, and claims that appear in the transcript.
    Do not invent or assume any information not explicitly present.
    If you are uncertain about a detail, omit it rather than guess.

    Transcript:

    {{ transcript }}
  "#
}

// Map phase — summarize one chunk of a long transcript.
function SummarizeChunk(chunk: string, chunk_index: int, total_chunks: int) -> string {
  client Ollama
  prompt #"
    Summarize this section of a transcript. Preserve key facts, data points,
    quotes, and conclusions. Be thorough — this is section {{ chunk_index }} of {{ total_chunks }}.

    Only include information explicitly stated in the transcript.
    Do not invent names, quotes, statistics, or facts not present in the text.

    Transcript section {{ chunk_index }}/{{ total_chunks }}:

    {{ chunk }}
  "#
}

// Reduce phase — combine chunk summaries into a final summary.
function ReduceChunkSummaries(section_summaries: string, artist: string, video_title: string) -> string {
  client Ollama
  prompt #"
    You are a helpful assistant that summarizes YouTube video transcripts.
    Artist/channel context: {{ artist }}
    Video title: {{ video_title }}

    The following are summaries of consecutive sections of a transcript.
    Combine them into a single coherent summary, preserving all key points.

    Only include facts and claims from the section summaries below.
    Do not add new information, names, or details not found in these summaries.

    {{ section_summaries }}
  "#
}

// Refine phase — iteratively update a rolling summary with a new chunk.
function RefineSummary(prev_summary: string, chunk: string, chunk_index: int, total_chunks: int) -> string {
  client Ollama
  prompt #"
    You have a summary so far and a new section of transcript.
    Update the summary to incorporate the key points from this new section.
    Preserve all important information from the existing summary.

    Only include facts, names, and quotes that appear in the existing summary
    or in the new transcript section below. Do not invent any details.

    Current summary:
    {{ prev_summary }}

    New transcript section ({{ chunk_index }}/{{ total_chunks }}):
    {{ chunk }}
  "#
}
