#!/usr/bin/env bash
set -e

# @describe Get an answer to a question using Wolfram Alpha. Input should the query in English.
# Use it to answer user questions that require computation, detailed facts, data analysis, or complex queries.
# This ensures accurate and precise answers.

# @option --query! The query to search for.
# @env WOLFRAM_API_ID!

main() {
  local curl_args=(
    -sSf -G
    --data-urlencode "output=JSON"
    --data-urlencode "format=plaintext"
    --data-urlencode "input=$argc_query"
    --data-urlencode "appid=$WOLFRAM_API_ID"
    "https://api.wolframalpha.com/v2/query"
  )
  curl "${curl_args[@]}" | \
  jq -r '.queryresult.pods[] | select(.subpods[0].plaintext != "")'
}

eval "$(argc --argc-eval "$0" "$@")"
