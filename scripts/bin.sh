#!/usr/bin/env bash
set -e

export LLM_FUNCTIONS_DIR="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"

if [[ -f "$LLM_FUNCTIONS_DIR/.env" ]]; then
    source "$LLM_FUNCTIONS_DIR/.env"
fi

if [[ "$0" == *bin.sh ]]; then
    FUNC_FILE="$1"
    FUNC_DATA="$2"
else
    FUNC_FILE="$(basename "$0")"
    FUNC_DATA="$1"
fi
if [[ "$FUNC_FILE" != *'.sh' ]]; then
    FUNC_FILE="$FUNC_FILE.sh"
fi

FUNC_FILE="$LLM_FUNCTIONS_DIR/tools/$FUNC_FILE"

if [[ "$OS" == "Windows_NT" ]]; then
    FUNC_FILE="$(cygpath -w "$FUNC_FILE")"
fi

if [[ "$LLM_FUNCTION_ACTION" == "declarate" ]]; then
    argc --argc-export "$FUNC_FILE" | \
    jq -r '
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
    if [[ -z "$FUNC_DATA" ]]; then
        echo "No json data"
        exit 1
    fi

    data="$(
        echo "$FUNC_DATA" | \
        jq -r '
        to_entries | .[] | 
        (.key | split("_") | join("-")) as $key |
        if .value | type == "array" then
            .value | .[] | "--\($key)\n\(.)"
        elif .value | type == "boolean" then
            if .value then "--\($key)" else "" end
        else
            "--\($key)\n\(.value)"
        end' | \
        tr -d '\r'
    )" || {
        echo "Invalid json data"
        exit 1
    }
    while IFS= read -r line; do
        ARGS+=("$line")
    done <<< "$data"
    "$FUNC_FILE" "${ARGS[@]}"
fi