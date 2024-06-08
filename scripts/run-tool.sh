#!/usr/bin/env bash
set -e

main() {
    this_file_name=run-tool.sh
    parse_argv "$@"
    root_dir="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"
    setup_env
    tool_path="$root_dir/tools/$tool_name.sh"
    run
}

parse_argv() {
    if [[ "$0" == *"$this_file_name" ]]; then
        tool_name="$1"
        tool_data="$2"
    else
        tool_name="$(basename "$0")"
        tool_data="$1"
    fi
    if [[ "$tool_name" == *.sh ]]; then
        tool_name="${tool_name:0:$((${#tool_name}-3))}"
    fi
}

setup_env() {
    export LLM_ROOT_DIR="$root_dir"
    if [[ -f "$LLM_ROOT_DIR/.env" ]]; then
        source "$LLM_ROOT_DIR/.env"
    fi
    export LLM_TOOL_NAME="$tool_name"
    export LLM_TOOL_CACHE_DIR="$LLM_ROOT_DIR/cache/$tool_name"
}

run() {
    if [[ -z "$tool_data" ]]; then
        die "No JSON data"
    fi

    _jq=jq
    if [[ "$OS" == "Windows_NT" ]]; then
        _jq="jq -b"
        tool_path="$(cygpath -w "$tool_path")"
    fi

    data="$(
        echo "$tool_data" | \
        $_jq -r '
        to_entries | .[] | 
        (.key | split("_") | join("-")) as $key |
        if .value | type == "array" then
            .value | .[] | "--\($key)\n\(. | @json)"
        elif .value | type == "boolean" then
            if .value then "--\($key)" else "" end
        else
            "--\($key)\n\(.value | @json)"
        end'
    )" || {
        die "Invalid JSON data"
    }
    while IFS= read -r line; do
        if [[ "$line" == '--'* ]]; then
            args+=("$line")
        else
            args+=("$(echo "$line" | $_jq -r '.')")
        fi
    done <<< "$data"
    "$tool_path" "${args[@]}"
}

die() {
    echo "$*" >&2
    exit 1
}

main "$@"
