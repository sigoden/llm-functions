#!/usr/bin/env bash
set -e

# @describe Get an answer to a question using Wolfram Alpha. Input should the query in English.
# Use it to answer user questions that require computation, detailed facts, data analysis, or complex queries.

# @env WOLFRAM_API_ID! The api id
# @option --query! The query to search for.

main() {
    encoded_query="$(jq -nr --arg q "$argc_query" '$q|@uri')"
    url="https://api.wolframalpha.com/v2/query?appid=$WOLFRAM_API_ID&input=$encoded_query&output=json&format=plaintext"
    curl -fsSL "$url" | jq '[.queryresult | .pods[] | {title:.title, values:[.subpods[].plaintext | select(. != "")]}]' \
    >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
