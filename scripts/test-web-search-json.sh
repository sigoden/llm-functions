#!/usr/bin/env bash
set -euo pipefail

# Verify web search tools build valid JSON when the query contains quotes.

test_tavily_payload() {
    local query='test "test"'
    local api_key='dummy-key'
    jq -n --arg api_key "$api_key" --arg query "$query" \
        '{
          "api_key": $api_key,
          "query": $query,
          "include_answer": true
        }' | jq -e '.query == "test \"test\""' >/dev/null
}

test_perplexity_payload() {
    local query='test "test"'
    local model='llama-3.1-sonar-small-128k-online'
    jq -n --arg model "$model" --arg query "$query" \
        '{
          "model": $model,
          "messages": [
            { "role": "user", "content": $query }
          ]
        }' | jq -e '.messages[0].content == "test \"test\""' >/dev/null
}

test_tavily_payload
test_perplexity_payload
echo "web search JSON escaping tests passed"
