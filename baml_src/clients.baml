// yt-artist LLM client configuration.
// Default: Ollama (local). Set OPENAI_API_KEY to use OpenAI.

// Local Ollama instance — default for yt-artist.
// Override model via OPENAI_MODEL env var in the adapter layer.
client<llm> Ollama {
  provider openai-generic
  retry_policy Exponential
  options {
    base_url "http://localhost:11434/v1"
    model "mistral"
    default_role "user"
  }
}

// OpenAI — activated when OPENAI_API_KEY is set.
client<llm> OpenAI {
  provider openai
  retry_policy Exponential
  options {
    model "gpt-4o-mini"
    api_key env.OPENAI_API_KEY
  }
}

retry_policy Exponential {
  max_retries 3
  strategy {
    type exponential_backoff
    delay_ms 1000
    multiplier 2.0
    max_delay_ms 30000
  }
}
