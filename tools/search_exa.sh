#!/usr/bin/env bash
set -e

# @describe Perform a web search using Exa API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env EXA_API_KEY! The api key
# @env EXA_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
    curl -fsSL -X POST https://api.exa.ai/search \
        -H "content-type: application/json" \
        -H "x-api-key: $EXA_API_KEY" \
        -d '
{
    "query": "'"$argc_query"'",
    "numResults": '"$EXA_MAX_RESULTS"',
    "type": "keyword",
    "contents": {
        "text": {
            "maxCharacters": 200
        }
    }
}' | \
        jq '[.results[] | {title: .title, url: .url, text: .text}]'
}

eval "$(argc --argc-eval "$0" "$@")"


