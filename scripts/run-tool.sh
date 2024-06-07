#!/usr/bin/env bash
set -e

export LLM_FUNCTIONS_DIR="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"

if [[ -f "$LLM_FUNCTIONS_DIR/.env" ]]; then
    source "$LLM_FUNCTIONS_DIR/.env"
fi

if [[ "$0" == *run-tool.sh ]]; then
    func_name="$1"
    func_data="$2"
else
    func_name="$(basename "$0")"
    func_data="$1"
fi
if [[ "$func_name" == *.sh ]]; then
    func_name="${func_name:0:$((${#func_name}-3))}"
fi

export LLM_FUNCTION_NAME="$func_name"
func_file="$LLM_FUNCTIONS_DIR/tools/$func_name.sh"

export JQ=jq
if [[ "$OS" == "Windows_NT" ]]; then
    export JQ="jq -b"
    func_file="$(cygpath -w "$func_file")"
fi

if [[ -z "$func_data" ]]; then
    echo "No json data"
    exit 1
fi

data="$(
    echo "$func_data" | \
    $JQ -r '
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
        args+=("$(echo "$line" | $JQ -r '.')")
    fi
done <<< "$data"
"$func_file" "${args[@]}"