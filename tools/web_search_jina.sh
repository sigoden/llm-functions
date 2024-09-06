#!/usr/bin/env bash
set -e

# @describe Perform a web search using the Jina Search API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env JINA_API_KEY The api key
# @option --query! The query to search for.

main() {
    curl_args=()
    if [[ -n "$JINA_API_KEY" ]]; then
        curl_args+=("-H" "Authorization: Bearer $JINA_API_KEY")
        curl_args+=("-H" "X-Return-Format: markdown")
    fi

    query="$argc_query"
    encoded_query=$(printf "%s" "$query" | sed -e 's/ /%20/g' -e 's/&/%26/g' -e 's/?/%3F/g')

    curl -fsSL "${curl_args[@]}" "https://s.jina.ai/$encoded_query" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
