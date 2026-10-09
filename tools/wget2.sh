#!/data/data/com.termux/files/usr/bin/env bash
set -euo pipefail

# @describe Download files or websites using GNU Wget2
# @option --url! The URL to download
# @option --output-file -O File to save the output
# @option --user-agent Custom User-Agent header
# @option --limit-rate Limit bandwidth usage (e.g., 100k, 1M)
# @option --tries -t Number of retries (default: 20)
# @option --waitretry Wait specified seconds between retries
# @flag --quiet -q Quiet mode
# @flag --verbose -v Verbose mode
# @flag --mirror -m Mirror a website (recursive)
# @flag --no-check-certificate Don't validate the server's certificate
# @env LLM_OUTPUT=/dev/stdout The output path.

main() {
    local output_target="${LLM_OUTPUT:-/dev/stdout}"
    local wget2_args=()
    
    # Termux CA Certs path
    local termux_certs="/data/data/com.termux/files/usr/etc/tls/cert.pem"
    if [[ -f "$termux_certs" ]]; then
        wget2_args+=("--ca-certificate" "$termux_certs")
    fi

    wget2_args+=("--no-cookies")

    [[ "${argc_no_check_certificate:-false}" == "true" ]] && wget2_args+=("--no-check-certificate")
    [[ -n "${argc_output_file:-}" ]] && wget2_args+=("-O" "$argc_output_file")
    [[ -n "${argc_user_agent:-}" ]] && wget2_args+=("--user-agent" "$argc_user_agent")
    [[ "${argc_quiet:-false}" == "true" ]] && wget2_args+=("-q")
    [[ "${argc_verbose:-false}" == "true" ]] && wget2_args+=("-v")
    [[ "${argc_mirror:-false}" == "true" ]] && wget2_args+=("-m")
    [[ -n "${argc_limit_rate:-}" ]] && wget2_args+=("--limit-rate" "$argc_limit_rate")
    [[ -n "${argc_tries:-}" ]] && wget2_args+=("-t" "$argc_tries")
    [[ -n "${argc_waitretry:-}" ]] && wget2_args+=("--waitretry" "$argc_waitretry")

    # Execute wget2
    if ! wget2 "${wget2_args[@]}" "$argc_url"; then
        echo "{\"status\": \"error\", \"msg\": \"wget2 download failed for $argc_url\"}" > "$output_target"
        exit 1
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
