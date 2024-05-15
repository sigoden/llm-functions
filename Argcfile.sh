#!/usr/bin/env bash
set -e

# @meta dotenv


# @cmd Call the function
# @arg func![`_choice_func`] The function name
# @arg args~[?`_choice_func_args`] The function args
call() {
    "./bin/$argc_func" "${argc_args[@]}"
}

# @cmd Build declarations for specific functions
# @option --output=functions.json <FILE> Specify a file path to save the function declarations
# @option --names-file=functions.txt Specify a file containing function names
# @arg funcs*[`_choice_func`] The function names
build-declarations() {
    if [[ "${#argc_funcs[@]}" -gt 0 ]]; then
        names=("${argc_funcs[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file"))
    fi
    if [[ -z "$names" ]]; then
        _die "error: no specific function"
    fi
    result=()
    for name in "${names[@]}"; do
        result+=("$(build-func-declaration "$name")")
    done
    echo "["$(IFS=,; echo "${result[*]}")"]"  | jq '.' > "$argc_output"
}

# @cmd
# @arg func![`_choice_func`] The function name
build-func-declaration() {
    argc --argc-export bin/$1 | \
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
}

_choice_func() {
    ls -1 bin 
}

_choice_func_args() {
    args=( "${argc__positionals[@]}" )
    argc --argc-compgen generic "bin/${args[0]}" "${args[@]}"
}

_die() {
    echo "$*"
    exit 1
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
