#!/usr/bin/env bash
set -e

# @describe Searche Wikipedia for a query.
# Uses it to get detailed information about a public figure, interpretation of a complex scientific concept or in-depth connectivity of a significant historical event,.

# @option --query! The query to search for.

main() {
    encoded_query="$(jq -nr --arg q "$argc_query" '$q|@uri')"
    search_url="https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=$encoded_query&format=json"
    title="$(curl -fsSL "$search_url" | jq -r '.query.search[0].title' 2>/dev/null)"
    if [[ -z "$title" ]]; then
        echo "Error: No results found for '$argc_query'"
        exit 1
    fi
    title="$(echo "$title" | tr ' ' '_')"
    page_url="https://en.wikipedia.org/api/rest_v1/page/summary/$title"
    summary="$(curl -fsSL "$page_url"  | jq -r '.extract')"
    echo '{
    "link": "https://en.wikipedia.org/wiki/'"$title"'",
    "summary": "'"$summary"'"
}'
}

eval "$(argc --argc-eval "$0" "$@")"
