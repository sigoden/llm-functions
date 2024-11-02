#!/usr/bin/env bash
set -e

# @describe Perform a web search to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @option --query! The query to search for.

# @meta require-tools aichat
# @env WEB_SEARCH_MODEL! The model for web-searching.
#
# supported aichat models:
#   - cohere:*
#   - vertexai:gemini-*
#   - perplexity:*-online
#   - ernie:*
#   - lingyiwanwu:yi-large-rag

main() {
    client="${WEB_SEARCH_MODEL%%:*}"
    case "$client" in
    cohere)
        export AICHAT_PATCH_COHERE_CHAT_COMPLETIONS='{".*":{"body":{"connectors":[{"id":"web-search"}]}}}'
        ;;
    vertexai)
        export AICHAT_PATCH_VERTEXAI_CHAT_COMPLETIONS='{"gemini-.*":{"body":{"tools":[{"googleSearchRetrieval":{}}]}}}'
        ;;
    esac
    aichat -m "$WEB_SEARCH_MODEL" "$argc_query" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
