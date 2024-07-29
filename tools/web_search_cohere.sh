#!/usr/bin/env bash
set -e

# @describe Perform a web search using Cohere API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env COHERE_API_KEY! The api key
# @option --query! The query to search for.

main() {
    curl -fsS -X POST https://api.cohere.com/v1/chat \
     -H "authorization: Bearer $COHERE_API_KEY" \
     -H "accept: application/json" \
     -H "content-type: application/json" \
     --data '
{
    "model": "command-r",
    "message": "'"$argc_query"'",
    "connectors": [{"id": "web-search"}]
}
'  | \
        jq -r '.text' \
        >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
