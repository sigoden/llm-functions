#!/usr/bin/env bash
set -e

# @describe Perform a web search to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @option --query! The query to search for.

# @meta require-tools aichat

# @env WEB_SEARCH_MODEL! The model for web-searching.
#
# supported aichat models:
#   - vertexai:gemini-*
#   - perplexity:*-online
#   - ernie:*
#   - lingyiwanwu:yi-large-rag
# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    client="${WEB_SEARCH_MODEL%%:*}"
    if [[ "$client" == "vertexai" ]]; then
        export AICHAT_PATCH_VERTEXAI_CHAT_COMPLETIONS='{"gemini-.*":{"body":{"tools":[{"googleSearchRetrieval":{}}]}}}'
    fi
    aichat -m "$WEB_SEARCH_MODEL" "$argc_query" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
