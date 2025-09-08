#!/usr/bin/env bash
set -e

# @describe Perform a web search using Tavily API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @option --query! The query to search for.

# @env TAVILY_API_KEY! The api key
# @env LLM_OUTPUT=/dev/stdout The output path The output path

main() {
    jq -n --arg api_key "$TAVILY_API_KEY" --arg query "$argc_query" \
        '{
            "api_key": $api_key,
            "query": $query,
            "include_answer": true
        }' |
        curl -fsSL -d @- \
            -X POST https://api.tavily.com/search \
            -H "content-type: application/json" |
        jq -r '.answer' \
            >>"$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
