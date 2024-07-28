#!/usr/bin/env bash
set -e

# @describe Perform a web search using Perplexity API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env PERPLEXITY_API_KEY! The api key
# @option --query! The query to search for.

main() {
    curl -fsS -X POST https://api.perplexity.ai/chat/completions \
     -H "authorization: Bearer $PERPLEXITY_API_KEY" \
     -H "accept: application/json" \
     -H "content-type: application/json" \
     --data '
{
  "model": "llama-3-sonar-small-32k-online",
  "messages": [
    {
      "role": "user",
      "content": "'"$argc_query"'"
    }
  ]
}
'  | \
        jq -r '.choices[0].message.content' \
        >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
