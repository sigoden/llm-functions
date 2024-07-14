#!/usr/bin/env bash
set -e

# @describe Perform a web search using Jina API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env JINA_API_KEY The api key
# @env SEARCH_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
    curl_args=("-H" "Accept: application/json")
    if [[ -n "$JINA_API_KEY" ]]; then
        curl_args+=("-H" "Authorization: Bearer $JINA_API_KEY")
    fi
    encoded_query="$(jq -nr --arg q "$argc_query" '$q|@uri')"
    curl -fsSL "${curl_args[@]}" "https://s.jina.ai/$encoded_query" | \
        jq '[.data[:'"$SEARCH_MAX_RESULTS"'] | .[] | {link: .url, title: .title, snippet: .description}]' \
        >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
