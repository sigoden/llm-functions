#!/usr/bin/env bash
set -e

main() {
    this_file_name=run-agent.sh
    parse_argv "$@"
    root_dir="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"
    setup_env
    agent_tools_path="$root_dir/agents/$agent_name/tools.sh"
    run
}

parse_argv() {
    if [[ "$0" == *"$this_file_name" ]]; then
        agent_name="$1"
        agent_func="$2"
        agent_data="$3"
    else
        agent_name="$(basename "$0")"
        agent_func="$1"
        agent_data="$2"
    fi
    if [[ "$agent_name" == *.sh ]]; then
        agent_name="${agent_name:0:$((${#agent_name}-3))}"
    fi
}

setup_env() {
    export LLM_ROOT_DIR="$root_dir"
    if [[ -f "$LLM_ROOT_DIR/.env" ]]; then
        source "$LLM_ROOT_DIR/.env"
    fi
    export LLM_AGENT_NAME="$agent_name"
    export LLM_AGENT_ROOT_DIR="$LLM_ROOT_DIR/agents/$agent_name"
    export LLM_AGENT_CACHE_DIR="$LLM_ROOT_DIR/cache/$agent_name"
}

run() {
    if [[ -z "$agent_data" ]]; then
        die "No JSON data"
    fi

    _jq=jq
    if [[ "$OS" == "Windows_NT" ]]; then
        _jq="jq -b"
        agent_tools_path="$(cygpath -w "$agent_tools_path")"
    fi

    data="$(
        echo "$agent_data" | \
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
    "$agent_tools_path" "$agent_func" "${args[@]}"
}

die() {
    echo "$*" >&2
    exit 1
}

main "$@"

