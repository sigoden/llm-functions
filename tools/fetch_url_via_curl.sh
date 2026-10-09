#!/data/data/com.termux/files/usr/bin/env bash
set -euo pipefail

# @describe Fetch content from a URL using curl with advanced options.
# @option --url! The URL to fetch.
# @option --timeout=30 <INT> Request timeout in seconds.
# @option --max-time=60 <INT> Maximum transfer time in seconds.
# @option --connect-timeout=10 <INT> Connection timeout in seconds.
# @option --user-agent=curl_tool/1.0 Custom User-Agent header.
# @option --output= Output file path (default: LLM_OUTPUT or stdout).
# @option --dump-header= Output file path for HTTP response headers.
# @option --cookie-file= Path to cookie file for session management.
# @option --method=GET HTTP method (GET, POST, PUT, DELETE, PATCH).
# @option --data= Request body data for POST/PUT.
# @option --headers= Comma-separated headers (e.g., "Key: Val,Key2: Val2").
# @flag --follow-redirects Follow HTTP redirects.
# @option --max-redirects=5 <INT> Maximum number of redirects.
# @flag --verify-ssl Verify SSL certificates (default: true).
# @flag --compressed Request compressed response.
# @flag --silent Silent mode.
# @flag --verbose Verbose output.
# @flag --include Include HTTP headers in output.
# @option --retry=3 <INT> Number of retry attempts.
# @option --retry-delay=1 <INT> Delay between retries in seconds.
# @option --limit-rate= Limit download rate (e.g., 100k, 1m).
# @option --proxy= Proxy server URL.
# @option --auth= Authentication credentials (user:password or bearer token).
# @option --auth-type=basic Authentication type (basic or bearer).

# @env LLM_OUTPUT=/dev/stdout The output path.

main() {
    local output_target="${argc_output:-${LLM_OUTPUT:-/dev/stdout}}"
    
    # Initialize curl arguments array
    local curl_args=()
    
    # Build curl command
    build_curl_command
    
    # Execute curl
    execute_curl "${curl_args[@]}" "$output_target"
}

build_curl_command() {
    # Basic options
    curl_args+=(
        --location --show-error
        --max-time "${argc_max_time:-60}"
        --connect-timeout "${argc_connect_timeout:-10}"
        --user-agent "${argc_user_agent:-curl_tool/1.0}"
        --retry "${argc_retry:-3}"
        --retry-delay "${argc_retry_delay:-1}"
    )

    # Redirect handling
    if [[ "${argc_follow_redirects:-false}" == "true" ]]; then
        curl_args+=(--max-redirs "${argc_max_redirects:-5}")
    else
        curl_args+=(--no-location)
    fi

    # SSL verification
    if [[ "${argc_verify_ssl:-true}" != "true" ]]; then
        curl_args+=(--insecure)
    fi

    # Header and Cookie options
    [[ -n "${argc_dump_header:-}" ]] && curl_args+=(--dump-header "${argc_dump_header}")
    [[ -n "${argc_cookie_file:-}" ]] && curl_args+=(--cookie-jar "${argc_cookie_file}" --cookie "${argc_cookie_file}")

    # Other flags
    [[ "${argc_compressed:-false}" == "true" ]] && curl_args+=(--compressed)
    [[ "${argc_silent:-false}" == "true" ]] && curl_args+=(--silent)
    [[ "${argc_verbose:-false}" == "true" ]] && curl_args+=(--verbose)
    [[ "${argc_include:-false}" == "true" ]] && curl_args+=(--include)

    # Rate limiting
    [[ -n "${argc_limit_rate:-}" ]] && curl_args+=(--limit-rate "${argc_limit_rate}")

    # HTTP method
    [[ -n "${argc_method:-}" ]] && curl_args+=(--request "${argc_method}")

    # Request data
    [[ -n "${argc_data:-}" ]] && curl_args+=(--data "${argc_data}")

    # Headers
    if [[ -n "${argc_headers:-}" ]]; then
        IFS=',' read -ra headers_array <<< "$argc_headers"
        for header in "${headers_array[@]}"; do
            curl_args+=(--header "$header")
        done
    fi

    # Proxy
    [[ -n "${argc_proxy:-}" ]] && curl_args+=(--proxy "${argc_proxy}")

    # Authentication
    if [[ -n "${argc_auth:-}" ]]; then
        local auth_type="${argc_auth_type:-basic}"
        if [[ "$auth_type" == "basic" ]]; then
            curl_args+=(--user "${argc_auth}")
        elif [[ "$auth_type" == "bearer" ]]; then
            curl_args+=(--header "Authorization: Bearer ${argc_auth}")
        fi
    fi

    # Add URL
    curl_args+=("${argc_url}")
}

execute_curl() {
    local -a curl_args=("$@")
    local output_target="${curl_args[-1]}"
    unset 'curl_args[${#curl_args[@]}-1]'

    # Use a temp file for response to check HTTP code
    local tmp_file
    tmp_file=$(mktemp)
    
    local http_code
    http_code=$(curl -s -w "%{http_code}" "${curl_args[@]}" -o "$tmp_file")
    
    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        if [[ "$output_target" == "/dev/stdout" || "$output_target" == "-" ]]; then
            cat "$tmp_file"
        else
            cat "$tmp_file" > "$output_target"
        fi
        rm -f "$tmp_file"
    else
        if [[ "$output_target" == "/dev/stdout" || "$output_target" == "-" ]]; then
            echo "{"status": "error", "http_code": $http_code, "msg": "Request failed with status $http_code"}"
        else
            echo "{"status": "error", "http_code": $http_code, "msg": "Request failed with status $http_code"}" > "$output_target"
        fi
        rm -f "$tmp_file"
    fi
}


eval "$(argc --argc-eval "$0" "$@")"
