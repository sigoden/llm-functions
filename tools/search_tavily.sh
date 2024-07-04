#!/usr/bin/env bash
set -e

# @describe Perform a web search using Tavily API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env TAVILY_API_KEY! The max results to return.
# @env TAVILY_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
   curl -fsSL -X POST \
    -H 'content-type: application/json' \
    -d '{"api_key":"'"$TAVILY_API_KEY"'","query":"'"$argc_query"'","search_depth":"advanced","max_results":"'"$TAVILY_MAX_RESULTS"'"}' \
    https://api.tavily.com/search
}

eval "$(argc --argc-eval "$0" "$@")"

