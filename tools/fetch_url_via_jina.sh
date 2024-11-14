#!/usr/bin/env bash
set -e

# @describe Extract the content from a given URL.
# @option --url! The URL to scrape.

# @env JINA_API_KEY The api key
# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    curl_args=()
    if [[ -n "$JINA_API_KEY" ]]; then
        curl_args+=("-H" "Authorization: Bearer $JINA_API_KEY")
    fi
    curl -fsSL "${curl_args[@]}" "https://r.jina.ai/$argc_url" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
