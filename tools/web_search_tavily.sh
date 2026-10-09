#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Perform a web search using Tavily API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @option --query! The query to search for.

# @env TAVILY_API_KEY! The api key
# @env LLM_OUTPUT=/dev/fd/1 The output path The output path

main() {
    # This tool fetches search results as JSON using curl and extracts the 'answer'.
    # The output is not directly colorized by this script.
    curl -fsSL -X POST https://api.tavily.com/search \
        -H "content-type: application/json" \
        -d '
{
    "api_key": "'"$TAVILY_API_KEY"'",
    "query": "'"$argc_query"'",
    "include_answer": true
}' | \
    jq -r '.answer' >&1
}

eval "$(argc --argc-eval "$0" "$@")"
