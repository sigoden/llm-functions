#!/usr/bin/env bash
set -e

# @describe Fetch contents of a URI using Exa API.
# Use this when you need to get contents of a link, where you think relevant info is to be found.
# If you have several sources for a subject to fetch, prioritize personal blogs, PDF files, official documentation, science articles.

# @option --url! The query to search for.

# @env EXA_API_KEY! The api key
# @env LLM_OUTPUT=/dev/stdout The output path The output path


main() {
    # Make the API request and capture the full response
    response=$(curl -fsS -X POST 'https://api.exa.ai/contents' \
      -H "x-api-key: $EXA_API_KEY" \
      -H 'Content-Type: application/json' \
      -d '{
        "urls": ["'"$argc_url"'"],
        "text": true,
        "livecrawlTimeout": 10000
      }')

    # Check if curl command succeeded
    if [ $? -ne 0 ]; then
        echo "Error: Failed to make API request" >> "$LLM_OUTPUT"
        exit 0
    fi

    # Extract the status information
    status=$(echo "$response" | jq -r '.statuses[0].status')
    error_tag=$(echo "$response" | jq -r '.statuses[0].error.tag // "none"')
    http_status=$(echo "$response" | jq -r '.statuses[0].error.httpStatusCode // 200')

    # Check if the status indicates an error
    if [ "$status" = "error" ] || [ "$http_status" != "200" ]; then
        echo "Error: This page is unavailable" >> "$LLM_OUTPUT"
        exit 0
    fi

    # If successful, extract and output the text
    echo "$response" | jq -r '.results[0].text' >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"

