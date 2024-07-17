#!/usr/bin/env bash
set -e

# @describe Perform a web search using Brave Search API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env BRAVE_API_KEY! The api key
# @env SEARCH_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
    encoded_query="$(jq -nr --arg q "$argc_query" '$q|@uri')"
    url="https://api.search.brave.com/res/v1/web/search?q=$encoded_query&count=$SEARCH_MAX_RESULTS"
    curl -fsSL "$url" \
        -H "Accept: application/json" \
        -H "X-Subscription-Token: $BRAVE_API_KEY" | \
        jq '[.web.results[] | {link: .url, title: .title, snippet: .description}]' \
        >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
