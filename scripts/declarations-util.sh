#!/usr/bin/env bash

set -e

# @cmd Pretty print declarations
#
# Examples:
#   ./scripts/declarations.sh pretty-print functions.json
#   cat functions.json | ./scripts/declarations.sh pretty-print functions.json
# @flag --no-type Do not to display param type info
# @arg json-file The json file, Read stdin if omitted
pretty-print() {
    _run _pretty_print
}

# @cmd Generate placeholder json according to declarations
# Examples:
#   ./scripts/declarations.sh generate-json-data functions.json
#   cat functions.json | ./scripts/declarations.sh generate-json-data functions.json
# @arg json-file The json file, Read stdin if omitted
generate-json() {
    _run _generate_json
}

_run() {
    func="$1"
    _get_declarations_data
    if [[ "$json_type" == "object" ]]; then
        echo "$json_data" | $func
    elif [[ "$json_type" == "array" ]]; then
        for i in $(seq 1 $json_array_len); do
            echo "$json_data" | jq '.['$((i-1))']'  | $func
        done
    fi
}

_get_declarations_data() {
    if [[ -f "$argc_json_file" ]]; then
        json_data="$(cat "$argc_json_file")"
    else
        json_data="$(cat)"
    fi
    json_type="$(echo "$json_data" | jq -r '
if type == "array" then
    (. | length) as $len | "array;\($len)"
else
    if type == "object" then
        type
    else
        ""
    end
end
' 2>/dev/null || true)"
    if [[ "$json_type" == *object* ]]; then
        :;
    elif [[ "$json_type" == *array* ]]; then
        json_array_len="${json_type#*;}"
        json_type="${json_type%%;*}"
        if [[ ! "$json_array_len" -gt 0 ]]; then
            json_type=""
        fi
    fi
    if [[ -z "$json_type" ]]; then
        echo "invalid JSON data"
        exit 1
    fi
}

_pretty_print() {
    jq --arg no_type "$argc_no_type" -r '
def get_type:
    .value.type as $type |
    (if .required then "" else "?" end) as $symbol |
    (.value.enum // []) as $enum |
    ([
        { condition: ($type == "array"), result: "string[]" },
        { condition: ($type == "string" and ($enum | length > 0)), result: ($enum | join("|")) },
        { condition: ($type == "string"), result: "" },
        { condition: true, result: $type }
    ] | map(select(.condition) | .result) | first) as $kind |
    if $kind != "" then "(\($kind))\($symbol)" else $symbol end;

def oneline_description: split("\n")[0];

def parse_property:
    .key as $key |
    (.value.description | oneline_description) as $description |
    (if $no_type != "1" then (. | get_type) else "" end) as $type |
    "  \($key)\($type): \($description)";

def print_params:
    .parameters | 
    .required as $requiredProperties |
    .properties | to_entries[] | 
    .key as $key | .+ { "required": ($requiredProperties | index($key) != null) } |
    parse_property;

def print_title:
    (.description | oneline_description) as $description |
    "\(.name): \($description)";

print_title, print_params
'
}

_generate_json() {
    jq -r -c '
def convert_string:
    if has("enum") then .enum[0] else "foo" end;

def parse_property:
    .key as $key |
    .value.type as $type |
    [
        { condition: ($type == "string"), result: { $key: (.value | convert_string) }},
        { condition: ($type == "boolean"), result: { $key: false }},
        { condition: ($type == "integer"), result: { $key: 42 }},
        { condition: ($type == "number"), result: { $key: 3.14 }},
        { condition: ($type == "array"), result: { $key: [ "v1" ] } }
    ] | map(select(.condition) | .result) | first;

.name,
(
    .parameters |
    [
        .properties | to_entries[] | parse_property
    ] | add // {}
)
'
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
