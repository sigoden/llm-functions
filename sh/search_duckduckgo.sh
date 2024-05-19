#!/usr/bin/env bash
set -e

# @describe Takes in a query string and returns search result from DuckDuckGo.
# Use it to answer user questions that require dates, facts, real-time information, or news.
# This ensures accurate and up-to-date answers.

# @meta require-tools ddgr
# @env DDG_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
    ddgr -n $DDG_MAX_RESULTS --json "$argc_query"
}

eval "$(argc --argc-eval "$0" "$@")"
