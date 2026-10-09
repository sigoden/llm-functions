#!/data/data/com.termux/files/usr/bin/env bash
set -euo pipefail

# @describe Extract the content from a URL using Jina AI Reader.
# @option --url!              The URL to scrape.
# @option --timeout=30 <INT>  Request timeout.
# @option --format=text       Output format (text, json).

# @env JINA_API_KEY           Optional API key.
# @env LLM_OUTPUT=/dev/stdout The output path.

main() {
    local output_target="${LLM_OUTPUT:-/dev/stdout}"
    local format="${argc_format:-text}"
    local target_url="https://r.jina.ai/${argc_url}"

    curl_args=(
        -fsSL --max-time "${argc_timeout:-30}"
        -H "Accept: application/${format}"
        -A "fetch_url_script/2.0"
    )

    [[ -n "${JINA_API_KEY:-}" ]] && curl_args+=(-H "Authorization: Bearer ${JINA_API_KEY}")

    curl "${curl_args[@]}" "$target_url" > "$output_target"
}

eval "$(argc --argc-eval "$0" "$@")"
