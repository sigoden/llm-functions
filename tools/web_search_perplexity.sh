#!/usr/bin/env bash
set -e

# @describe Perform a web search using Perplexity API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @option --query! The query to search for.

# @env PERPLEXITY_API_KEY! The api key
# @env PERPLEXITY_WEB_SEARCH_MODEL=llama-3.1-sonar-small-128k-online The LLM model for web search
# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    jq -n --arg model "${PERPLEXITY_WEB_SEARCH_MODEL}" --arg query "$argc_query" \
        '{
            "model": $model,
            "messages": [
                { "role": "user", "content": $query }
            ]
        }' |
        curl -fsSL --d @- \
            -X POST https://api.perplexity.ai/chat/completions \
            -H "authorization: Bearer $PERPLEXITY_API_KEY" \
            -H "accept: application/json" \
            -H "content-type: application/json" |
        jq -r '.choices[0].message.content' \
            >>"$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
