#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Make HTTP requests using curl with support for various methods, headers, authentication, and data.
# @option --url! The target URL for the HTTP request
# @option --method[GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS] HTTP method to use (default: GET)
# @option --headers* Custom headers as key:value pairs (can be specified multiple times)
# @option --data Request body data for POST/PUT/PATCH requests
# @option --data-file Path to file containing request body data
# @option --auth Authentication credentials in format "user:password" or bearer token
# @option --auth-type[basic|bearer] Authentication type (default: basic)
# @option --timeout <INT> Request timeout in seconds (default: 30)
# @option --content-type Content-Type header for request body (default: application/json)
# @option --user-agent Custom User-Agent header
# @option --output-file Path to save response body (default: stdout)
# @option --proxy Proxy server URL
# @option --cookie* Send cookies (name=value)
# @option --cookie-file Path to file containing cookies
# @option --form* Multipart form data (name=content)
# @option --max-redirs <INT> Maximum number of redirects
# @flag --follow Follow HTTP redirects
# @flag --verbose Enable verbose output for debugging
# @flag --include Include HTTP response headers in output
# @flag --silent Silent mode
# @flag --insecure Skip SSL certificate verification
# @flag --compressed Request compressed response

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    # Parse JSON input if provided
    if [[ "$1" == "{"* ]]; then
        local json_args="$1"
        parse_json_args "$json_args"
    fi

    # Set default values
    local method="${argc_method:-GET}"
    local timeout="${argc_timeout:-30}"
    local content_type="${argc_content_type:-application/json}"
    local output_file="${argc_output_file:-}"

    # Initialize curl arguments array
    local curl_args=()

    # Build curl command
    build_curl_command "$method" "$timeout" "$content_type"

    # Add URL
    curl_args+=("$argc_url")

    # Execute curl command
    execute_curl "${curl_args[@]}" "$output_file"
}

parse_json_args() {
    local json_args="$1"
    argc_url=$(echo "$json_args" | grep -o '"url": *"[^"]*"' | cut -d'"' -f4)
    argc_method=$(echo "$json_args" | grep -o '"method": *"[^"]*"' | cut -d'"' -f4)
    argc_verbose=$(echo "$json_args" | grep -o '"verbose": *[^,}]*' | cut -d' ' -f2)
    # Parse other fields as needed
}

build_curl_command() {
    local method="$1"
    local timeout="$2"
    local content_type="$3"

    # Basic options
    curl_args+=("-X" "$method")
    curl_args+=("--max-time" "$timeout")

    # Headers
    if [[ -n "$argc_headers" ]]; then
        for header in "${argc_headers[@]}"; do
            curl_args+=("-H" "$header")
        done
    fi

    # Content-Type header
    curl_args+=("-H" "Content-Type: $content_type")

    # User-Agent
    if [[ -n "$argc_user_agent" ]]; then
        curl_args+=("-H" "User-Agent: $argc_user_agent")
    fi

    # Authentication
    if [[ -n "$argc_auth" ]]; then
        local auth_type="${argc_auth_type:-basic}"
        if [[ "$auth_type" == "basic" ]]; then
            curl_args+=("-u" "$argc_auth")
        elif [[ "$auth_type" == "bearer" ]]; then
            curl_args+=("-H" "Authorization: Bearer $argc_auth")
        fi
    fi

    # Data options
    if [[ -n "$argc_data" ]]; then
        curl_args+=("-d" "$argc_data")
    elif [[ -n "$argc_data_file" ]]; then
        curl_args+=("--data-binary" "@$argc_data_file")
    fi

    # Form data
    if [[ -n "$argc_form" ]]; then
        for form_data in "${argc_form[@]}"; do
            curl_args+=("-F" "$form_data")
        done
    fi

    # Proxy
    if [[ -n "$argc_proxy" ]]; then
        curl_args+=("--proxy" "$argc_proxy")
    fi

    # Cookies
    if [[ -n "$argc_cookie" ]]; then
        for cookie in "${argc_cookie[@]}"; do
            curl_args+=("--cookie" "$cookie")
        done
    fi

    if [[ -n "$argc_cookie_file" ]]; then
        curl_args+=("--cookie-jar" "$argc_cookie_file")
    fi

    # Redirect options
    if [[ -n "$argc_follow" ]]; then
        curl_args+=("-L")
    fi

    if [[ -n "$argc_max_redirs" ]]; then
        curl_args+=("--max-redirs" "$argc_max_redirs")
    fi

    # Other flags
    if [[ -n "$argc_verbose" ]]; then
        curl_args+=("-v")
    fi

    if [[ -n "$argc_include" ]]; then
        curl_args+=("-i")
    fi

    if [[ -n "$argc_silent" ]]; then
        curl_args+=("-s")
    fi

    if [[ -n "$argc_insecure" ]]; then
        curl_args+=("-k")
    fi

    if [[ -n "$argc_compressed" ]]; then
        curl_args+=("--compressed")
    fi
}

execute_curl() {
    local -a curl_args=("$@")
    local output_file="${curl_args[-1]}"
    unset 'curl_args[${#curl_args[@]}-1]'

    if [[ -n "$output_file" ]]; then
        curl "${curl_args[@]}" > "$output_file"
    else
        curl "${curl_args[@]}"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
