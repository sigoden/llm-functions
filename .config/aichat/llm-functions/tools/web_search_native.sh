#!/usr/bin/env bash
set -e

# @describe Perform web search using Google search results with no API keys required
# @option --query! The search query string
# @option --limit <INT> Number of results to return (default: 5, max: 10)

WEB_SEARCH_SERVER="${WEB_SEARCH_SERVER:-/data/data/com.termux/files/home/web-search/build/index.js}"

main() {
    local limit="${argc_limit:-5}"

    # Validate limit: must be a number between 1 and 10
    if ! [[ "$limit" =~ ^[0-9]+$ ]]; then
        limit=5
    elif (( limit < 1 )); then
        limit=1
    elif (( limit > 10 )); then
        limit=10
    fi

    if ! command -v node >/dev/null 2>&1; then
        echo "ERROR: node is required for web_search_native but was not found in PATH" >&2
        exit 127
    fi

    if [[ ! -f "$WEB_SEARCH_SERVER" ]]; then
        echo "ERROR: web-search server not found at: $WEB_SEARCH_SERVER" >&2
        echo "Install/fix the web-search MCP build, or point WEB_SEARCH_SERVER at its index.js" >&2
        exit 3
    fi

    # Call the web-search MCP server; honour LLM_OUTPUT, fall back to stdout
    node "$WEB_SEARCH_SERVER" "$argc_query" "$limit" >> "${LLM_OUTPUT:-/dev/stdout}"
}

eval "$(argc --argc-eval "$0" "$@")"
