#!/data/data/com.termux/files/usr/bin/env bash
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

    # Set default API Key if not provided via environment or .env
    export YOU_API_KEY="${YOU_API_KEY:-ydc-sk-3be25b63a354f86f-cZsqdcYZe3xHo2qxVUZxEmTI1wAzlfG8-23e9d3b8}"

    if [[ -z "${YOU_API_KEY:-}" ]]; then
        echo "Error: YOU_API_KEY not found in environment variables or .env file" >&2
        exit 1
    fi

    # Execute search
    local response
    response=$(curl -s -G --compressed "https://ydc-index.io/v1/search" \
         -H "X-API-Key: $YOU_API_KEY" \
         -H "Accept-Encoding: gzip, deflate" \
         --data-urlencode "query=$query" \
         --data-urlencode "count=$limit" \
         --data-urlencode "offset=$offset" \
         --data-urlencode "safesearch=$safe_search" \
         ${include_domains:+--data-urlencode "include_domains=$include_domains"} \
         ${exclude_domains:+--data-urlencode "exclude_domains=$exclude_domains"} \
         ${country:+--data-urlencode "country=$country"})

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
