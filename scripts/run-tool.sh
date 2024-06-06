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

if [[ "$LLM_FUNCTION_ACTION" == "declarate" ]]; then
    argc --argc-export "$func_file" | \
    $JQ -r '
    def parse_description(flag_option):
        if flag_option.describe == "" then
            {}
        else
            { "description": flag_option.describe }
        end;

    def parse_enum(flag_option):
        if flag_option.choice.type == "Values" then
            { "enum": flag_option.choice.data }
        else
            {}
        end;

    def parse_property(flag_option):
        [
            { condition: (flag_option.flag == true), result: { type: "boolean" } },
            { condition: (flag_option.multiple_occurs == true), result: { type: "array", items: { type: "string" } } },
            { condition: (flag_option.notations[0] == "INT"), result: { type: "integer" } },
            { condition: (flag_option.notations[0] == "NUM"), result: { type: "number" } },
            { condition: true, result: { type: "string" } } ]
        | map(select(.condition) | .result) | first 
        | (. + parse_description(flag_option))
        | (. + parse_enum(flag_option))
        ;


    def parse_parameter(flag_options):
        {
            type: "object",
            properties: (reduce flag_options[] as $item ({}; . + { ($item.id | sub("-"; "_"; "g")): parse_property($item) })),
            required: [flag_options[] | select(.required == true) | .id],
        };

    {
        name: (.name | sub("-"; "_"; "g")),
        description: .describe,
        parameters: parse_parameter([.flag_options[] | select(.id != "help" and .id != "version")])
    }'
else
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
fi