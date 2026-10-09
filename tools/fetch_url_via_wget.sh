#!/data/data/com.termux/files/usr/bin/env bash
set -euo pipefail

# @describe Fetch content from a URL using wget.
# @option --url!                      The URL to fetch.
# @option --timeout=30 <INT>          Request timeout in seconds.
# @option --tries=3 <INT>             Number of retry attempts.
# @option --output=                   Output file path (default: LLM_OUTPUT or stdout).
# @option --user-agent=wget_tool/1.0  Custom User-Agent.
# @option --method=GET                HTTP method.
# @option --body-data=                Request body data.
# @option --headers=                  Headers (comma-separated).
# @flag --follow-redirects            Follow redirects.
# @option --max-redirect=5 <INT>      Max redirects.
# @flag --no-check-certificate        Skip SSL verification.
# @flag --continue                    Continue partial downloads.
# @flag --quiet                       Quiet mode.
# @flag --verbose                     Verbose output.

# @env LLM_OUTPUT=/dev/stdout The output path.

main() {
    local output_target="${argc_output:-${LLM_OUTPUT:-/dev/stdout}}"
    
    wget_args=(
        --timeout="${argc_timeout:-30}"
        --tries="${argc_tries:-3}"
        --user-agent="${argc_user_agent:-wget_tool/1.0}"
        --no-directories --no-clobber
    )

    [[ "${argc_follow_redirects:-false}" == "true" ]] && wget_args+=(--max-redirect="${argc_max_redirect:-5}") || wget_args+=(--max-redirect=0)
    [[ "${argc_no_check_certificate:-false}" == "true" ]] && wget_args+=(--no-check-certificate)
    [[ "${argc_continue:-false}" == "true" ]] && wget_args+=(--continue)
    [[ "${argc_quiet:-false}" == "true" ]] && wget_args+=(--quiet)
    [[ "${argc_verbose:-false}" == "true" ]] && wget_args+=(--verbose)
    [[ -n "${argc_method:-}" ]] && wget_args+=(--method="${argc_method}")
    [[ -n "${argc_body_data:-}" ]] && wget_args+=(--body-data="${argc_body_data}")

    if [[ -n "${argc_headers:-}" ]]; then
        IFS=',' read -ra headers_array <<< "$argc_headers"
        for h in "${headers_array[@]}"; do wget_args+=(--header="$h"); done
    fi

    wget "${wget_args[@]}" --output-document="$output_target" "$argc_url"
}

eval "$(argc --argc-eval "$0" "$@")"
