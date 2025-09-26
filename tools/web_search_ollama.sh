#!/usr/bin/env bash
set -e

# @describe Perform a web search using Ollama API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @option --query! The query to search for.

# @env OLLAMA_API_KEY! The api key
# @env LLM_OUTPUT=/dev/stdout The output path The output path

main() {
  jq -n --arg query "$argc_query" \
    '{
    "query": $query
}' |
    curl -fsSL -d @- \
      -X POST https://ollama.com/api/web_search \
      --header "Authorization: Bearer $OLLAMA_API_KEY" | jq -r '.results' >>"$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
