#!/usr/bin/env bash
set -e

main() {
    this_file_name=run-bot.sh
    parse_argv "$@"
    root_dir="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"
    setup_env
    bot_tools_path="$root_dir/bots/$bot_name/tools.sh"
    run
}

parse_argv() {
    if [[ "$0" == *"$this_file_name" ]]; then
        bot_name="$1"
        bot_func="$3"
        bot_data="$3"
    else
        bot_name="$(basename "$0")"
        bot_func="$1"
        bot_data="$2"
    fi
    if [[ "$bot_name" == *.sh ]]; then
        bot_name="${bot_name:0:$((${#bot_name}-3))}"
    fi
}

setup_env() {
    export LLM_ROOT_DIR="$root_dir"
    if [[ -f "$LLM_ROOT_DIR/.env" ]]; then
        source "$LLM_ROOT_DIR/.env"
    fi
    export LLM_BOT_NAME="$bot_name"
    export LLM_BOT_ROOT_DIR="$LLM_ROOT_DIR/bots/$bot_name"
    export LLM_BOT_CACHE_DIR="$LLM_ROOT_DIR/cache/$bot_name"
}

run() {
    if [[ -z "$bot_data" ]]; then
        die "No JSON data"
    fi

    _jq=jq
    if [[ "$OS" == "Windows_NT" ]]; then
        _jq="jq -b"
        bot_tools_path="$(cygpath -w "$bot_tools_path")"
    fi

    data="$(
        echo "$bot_data" | \
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
    "$bot_tools_path" "$bot_func" "${args[@]}"
}

die() {
    echo "$*" >&2
    exit 1
}

main "$@"

