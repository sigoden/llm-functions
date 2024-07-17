#!/usr/bin/env bash
set -e

# @describe Perform a web search using EXA API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env TAVILY_API_KEY! The api key
# @env SEARCH_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
    curl -fsSL -X POST https://api.tavily.com/search \
        -H "content-type: application/json" \
        -d '
{
    "api_key": "'"$TAVILY_API_KEY"'",
    "query": "'"$argc_query"'",
    "search_depth": "advanced",
    "max_results": "'"$SEARCH_MAX_RESULTS"'"
}' | \
        jq '[.results[] | {link: .url, title: .title, snippet: .content}]' \
        >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
