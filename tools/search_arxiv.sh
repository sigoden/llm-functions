#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Search arXiv for a query and return the top papers.

# @option --query! The query to search for.

# @env ARXIV_MAX_RESULTS=3 The max results to return.
# @env LLM_OUTPUT=/dev/fd/1 The output path

main() {
    # This tool fetches search results as JSON using curl and parses with jq.
    # The output is not directly colorized by this script.
    encoded_query="$(jq -nr --arg q "$argc_query" '$q|@uri')"
    url="http://export.arxiv.org/api/query?search_query=all:$encoded_query&max_results=$ARXIV_MAX_RESULTS"
    curl -fsSL "$url" >&1
}

eval "$(argc --argc-eval "$0" "$@")"
