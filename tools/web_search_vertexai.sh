#!/usr/bin/env bash
set -e

# @describe Perform a web search using VertexAI Gemini API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env VERTEXAI_PROJECT_ID! The project id
# @env VERTEXAI_LOCATION! The location
# @env VERTEXAI_WEB_SEARCH_MODEL=gemini-1.5-pro-001 The LLM model for web search
# @option --query! The query to search for.
# @meta require-tools gcloud

main() {
    curl -fsSL https://$VERTEXAI_LOCATION-aiplatform.googleapis.com/v1beta1/projects/$VERTEXAI_PROJECT_ID/locations/$VERTEXAI_LOCATION/publishers/google/models/$VERTEXAI_WEB_SEARCH_MODEL:generateContent \
        -X POST \
        -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
        -H "Content-Type: application/json" \
        -d '
{
    "contents": [{
        "role": "user",
        "parts": [{
            "text": "'"$argc_query"'"
        }]
    }],
    "safetySettings": [
        {
            "category": "HARM_CATEGORY_HARASSMENT",
            "threshold": "BLOCK_ONLY_HIGH"
        },
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_ONLY_HIGH"
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_ONLY_HIGH"
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_ONLY_HIGH"
        }
    ],
    "tools": [{
        "googleSearchRetrieval": {}
    }]
  }' | \
    jq -r '.candidates[0].content.parts[0].text' >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
