#!/usr/bin/env bash
set -e

# @describe Perform a web search using Bing Web Search API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env BING_API_KEY! The api key
# @env BING_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
    encoded_query="$(jq -nr --arg q "$argc_query" '$q|@uri')"
    url="https://api.bing.microsoft.com/v7.0/search?q=$encoded_query&mkt=en-us&textdecorations=true&textformat=raw&count=$BING_MAX_RESULTS&offset=0"
    curl -fsSL "$url" \
        -H "Ocp-Apim-Subscription-Key: $BING_API_KEY" | \
        jq '[.webPages.value[] | {name: .name, url: .url, snippet: .snippet}]'
}

eval "$(argc --argc-eval "$0" "$@")"

