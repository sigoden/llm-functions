#!/usr/bin/env bash
set -e

# @describe Perform a web search using Google Search API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env GOOGLE_API_KEY! The api key
# @env GOOGLE_CSE_ID! The id of google search engine
# @env GOOGLE_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
    encoded_query="$(jq -nr --arg q "$argc_query" '$q|@uri')"
    url="https://www.googleapis.com/customsearch/v1?key=$GOOGLE_API_KEY&cx=$GOOGLE_CSE_ID&q=$encoded_query"
    curl -fsSL "$url" | \
        jq '[.items[:'"$GOOGLE_MAX_RESULTS"'] | .[] | {title: .title, link: .link, snippet: .snippet}]' \
        >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
