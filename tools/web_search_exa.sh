#!/usr/bin/env bash
set -e

# @describe Perform a web search using Exa API to get a list of links to fetch.
# Use this when you need current information or feel a search could provide a better answer.
# Construct the query out of keywords, not human sentences, sorted by relevance - just like a good Google query.
# This returns text, then URL for every result found. Judging by the title of the page, fetch relevant info.

# @option --query! The query to search for.

# @env EXA_API_KEY! The api key
# @env LLM_OUTPUT=/dev/stdout The output path The output path

main() {
    json_escaped_query=$(jq -R -s . <<< "$argc_query")

    response=$(curl -fsS -X POST 'https://api.exa.ai/search' \
      -H "x-api-key: $EXA_API_KEY" \
      -H 'Content-Type: application/json' \
      -d "{
          \"query\": $json_escaped_query,
          \"type\": \"keyword\",
          \"numResults\": 20
          }")

    # Check if curl command succeeded
    if [ $? -ne 0 ]; then
        echo "Error: Failed to execute search request" >> "$LLM_OUTPUT"
        return 0
    fi

    # Check if response contains results
    results_count=$(echo "$response" | jq -r '.results | length')

    if [ "$results_count" -eq 0 ]; then
        echo "No results found for query: $argc_query" >> "$LLM_OUTPUT"
    else
        # Process results normally
        echo "$response" | jq -r '.results[] | (.title, .url, "")' >> "$LLM_OUTPUT"
    fi
}


eval "$(argc --argc-eval "$0" "$@")"
