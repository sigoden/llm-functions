#!/usr/bin/env bash
set -e

if [[ "$0" == *cmd.sh ]]; then
    FUNC_NAME="$1"
else
    FUNC_NAME="$(basename "$0")"
fi
if [[ "$FUNC_NAME" != *'.sh' ]]; then
    FUNC_NAME="$FUNC_NAME.sh"
fi

PROJECT_DIR="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"
FUNC_FILE="$PROJECT_DIR/sh/$FUNC_NAME"
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
    while IFS= read -r line; do
        ARGS+=("$line")
    done <<< "$(
        echo "$LLM_FUNCTION_DATA" | \
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
    )"
    "$FUNC_FILE" "${ARGS[@]}"
fi