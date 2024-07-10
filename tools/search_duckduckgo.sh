#!/usr/bin/env bash
set -e

# @describe Perform a web search using DuckDuckGo API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @meta require-tools ddgr
# @env DDG_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
    ddgr -n $DDG_MAX_RESULTS --json "$argc_query" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
