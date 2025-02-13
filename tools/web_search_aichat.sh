#!/usr/bin/env bash
set -e

# @describe Perform a web search to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @option --query! The query to search for.

# @meta require-tools aichat

# @env WEB_SEARCH_MODEL! The model for web-searching.
#
# supported aichat models:
#   - gemini:gemini-2.0-*
#   - vertexai:gemini-*
#   - perplexity:*
#   - ernie:*
# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    client="${WEB_SEARCH_MODEL%%:*}"
    if [[ "$client" == "gemini" ]]; then
        export AICHAT_PATCH_GEMINI_CHAT_COMPLETIONS='{".*":{"body":{"tools":[{"google_search":{}}]}}}'
    elif [[ "$client" == "vertexai" ]]; then
        export AICHAT_PATCH_VERTEXAI_CHAT_COMPLETIONS='{
    "gemini-1.5-.*":{"body":{"tools":[{"googleSearchRetrieval":{}}]}},
    "gemini-2.0-.*":{"body":{"tools":[{"google_search":{}}]}}
}'
    elif [[ "$client" == "ernie" ]]; then
        export AICHAT_PATCH_ERNIE_CHAT_COMPLETIONS='{".*":{"body":{"web_search":{"enable":true}}}}'
    fi
    aichat -m "$WEB_SEARCH_MODEL" "$argc_query" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
