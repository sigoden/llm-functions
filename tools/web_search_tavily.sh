#!/usr/bin/env bash
set -e

# @describe Perform a web search using Tavily API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env TAVILY_API_KEY! The api key
# @option --query! The query to search for.

main() {
    curl -fsSL -X POST https://api.tavily.com/search \
        -H "content-type: application/json" \
        -d '
{
    "api_key": "'"$TAVILY_API_KEY"'",
    "query": "'"$argc_query"'",
    "include_answer": true
}' | \
    jq -r '.answer' >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
