#!/usr/bin/env bash
set -e

export LLM_ROOT_DIR="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"

if [[ -f "$LLM_ROOT_DIR/.env" ]]; then
    source "$LLM_ROOT_DIR/.env"
fi

if [[ "$0" == *run-tool.sh ]]; then
    tool_name="$1"
    tool_data="$2"
else
    tool_name="$(basename "$0")"
    tool_data="$1"
fi
if [[ "$tool_name" == *.sh ]]; then
    tool_name="${tool_name:0:$((${#tool_name}-3))}"
fi

export LLM_TOOL_NAME="$tool_name"
export LLM_TOOL_CACHE_DIR="$LLM_ROOT_DIR/cache/$tool_name"

tool_file="$LLM_ROOT_DIR/tools/$tool_name.sh"

_jq=jq
if [[ "$OS" == "Windows_NT" ]]; then
    _jq="jq -b"
    tool_file="$(cygpath -w "$tool_file")"
fi

if [[ -z "$tool_data" ]]; then
    echo "No json data"
    exit 1
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
    echo "Invalid json data"
    exit 1
}
while IFS= read -r line; do
    if [[ "$line" == '--'* ]]; then
        args+=("$line")
    else
        args+=("$(echo "$line" | $_jq -r '.')")
    fi
done <<< "$data"
"$tool_file" "${args[@]}"