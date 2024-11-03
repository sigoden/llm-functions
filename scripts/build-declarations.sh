#!/usr/bin/env bash

main() {
    scriptfile="$1"
    is_tool=false
    if [[ "$(dirname "$scriptfile")" == tools ]]; then
        is_tool=true
    fi
    if [[ "$is_tool" == "true" ]]; then
        expr='[.]' 
    else
        expr='.subcommands' 
    fi
    argc --argc-export "$scriptfile" | \
    jq "$expr" | \
    build_declarations
}

build_declarations() {
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
            required: [flag_options[] | select(.required == true) | .id | sub("-"; "_"; "g")],
        };

    def parse_declaration:
        {
            name: (.name | sub("-"; "_"; "g")),
            description: .describe,
            parameters: parse_parameter([.flag_options[] | select(.id != "help" and .id != "version")])
        };
    [
        .[] | parse_declaration | select(.name | startswith("_") | not) | select(.description != "")
    ]'
}

main "$@"