#!/usr/bin/env bash
set -e

# @describe Perform a high-quality web search using the You.com Search API.
# @option --query! Search query string
# @option --limit <INT> Default: 5, Max: 20 - Maximum number of results to return
# @option --offset <INT> Default: 0, Max: 9 - Number of pages to skip
# @option --include-domains Comma-separated list of domains to prioritize
# @option --exclude-domains Comma-separated list of domains to block
# @option --safe-search [off|moderate|strict] Default: moderate - Safe search level
# @option --country Country code (e.g. "US", "IN")

# @env LLM_OUTPUT The output path

main() {
    local query="$argc_query"
    local limit="${argc_limit:-5}"
    local offset="${argc_offset:-0}"
    local include_domains="${argc_include_domains:-}"
    local exclude_domains="${argc_exclude_domains:-}"
    local safe_search="${argc_safe_search:-moderate}"
    local country="${argc_country:-}"
    local output_path="${LLM_OUTPUT:-/dev/stdout}"

    # Load environment variables
    if [[ -f ".env" ]]; then
        source .env
    fi

    if [[ -z "${YOU_API_KEY:-}" ]]; then
        echo "Error: YOU_API_KEY not found in environment variables or .env file" >&2
        exit 1
    fi

    # Build JSON payload for POST request using jq
    local data
    data=$(jq -n \
        --arg q "$query" \
        --argjson c "$limit" \
        --argjson o "$offset" \
        --arg ss "$safe_search" \
        --arg id "$include_domains" \
        --arg ed "$exclude_domains" \
        --arg cy "$country" \
        '{
            query: $q, 
            count: $c, 
            offset: $o,
            safesearch: $ss
        } | 
        if $id != "" then . + {include_domains: ($id | split(",") | map(sub("^\\s+|\\s+$"; "")))} else . end |
        if $ed != "" then . + {exclude_domains: ($ed | split(",") | map(sub("^\\s+|\\s+$"; "")))} else . end |
        if $cy != "" then . + {country: $cy} else . end')

    # Execute search
    local response
    response=$(curl -s -X POST https://ydc-index.io/v1/search \
         -H "X-API-Key: $YOU_API_KEY" \
         -H "Content-Type: application/json" \
         -d "$data")

    # Check for errors in response
    if echo "$response" | jq -e '.error' >/dev/null; then
        local error_msg
        error_msg=$(echo "$response" | jq -r '.error.message // "Unknown search error"')
        echo "Search error: $error_msg" >&2
        exit 1
    fi

    # Format and write results
    if [[ "$output_path" == "/dev/stdout" ]]; then
        echo "$response" | jq -c '.results.web'
    else
        echo "$response" | jq -c '.results.web' >> "$output_path"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
