#!/usr/bin/env bash
set -e

# @describe Perform a web search using SearXNG API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env SEARXNG_API_BASE! The api url
# @env SEARCH_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
    encoded_query="$(jq -nr --arg q "$argc_query" '$q|@uri')"
    url="$SEARXNG_API_BASE/search?q=$encoded_query&categories=general&language=en-US&format=json"
    curl -fsSL "$url" | \
        jq '[.results[:'"$SEARCH_MAX_RESULTS"'] | .[] | {link: .url, title: .title, snippet: .content}]' \
        >> "$LLM_OUTPUT"

}

eval "$(argc --argc-eval "$0" "$@")"
