#!/usr/bin/env bash
set -e

# @meta dotenv

BIN_DIR="${BIN_DIR:-bin}"

LANG_CMDS=( \
    "sh:bash" \
    "js:node" \
    "py:python" \
    "rb:ruby" \
)

# @cmd Call the function
# @arg func![`_choice_func`] The function name
# @arg args~[?`_choice_func_args`] The function args
call() {
    basename="${argc_func%.*}"
    lang="${argc_func##*.}"
    func_path="./$lang/$basename.$lang"
    if [[ ! -e "$func_path" ]]; then
        _die "error: not found $argc_func"
    fi
    if [[ "$lang" == "sh" ]]; then
        "$func_path" "${argc_args[@]}"
    else
       "$(_lang_to_cmd "$lang")" "./cmd/cmd.$lang" "$argc_func"
    fi
}

# @cmd Build the project
build() {
    argc build-declarations-json
    argc build-bin
}

# @cmd Build bin dir 
build-bin() {
    if [[ ! -f functions.txt ]]; then
        _die 'no found functions.txt'
    fi
    mkdir -p "$BIN_DIR"
    names=($(cat functions.txt))
    invalid_names=()
    for name in "${names[@]}"; do
        basename="${name%.*}"
        lang="${name##*.}"
        func_file="$lang/$name"
        if [[  -f "$func_file" ]]; then
            if [[ "$OS" = "Windows_NT" ]]; then
                bin_file="$BIN_DIR/$basename.cmd" 
                if [[ "$lang" == sh ]]; then
                    _build_win_sh > "$bin_file"
                else
                    _build_win_lang $lang "$(_lang_to_cmd "$lang")"  > "$bin_file"
                fi
            else
                bin_file="$BIN_DIR/$basename" 
                if [[ "$lang" == sh ]]; then
                    ln -rs "$func_file" "$bin_file"
                else
                    ln -rs "cmd/cmd.$lang" "$bin_file"
                fi
            fi
        else
            invalid_names+=("$name")
        fi
    done
    if [[ -n "$invalid_names" ]]; then
        _die "error: missing following functions: ${invalid_names[*]}"
    fi
    echo "Build bin"
}

# @cmd Build declarations.json
# @option --output=functions.json <FILE> Path to a json file to save function declarations
# @option --names-file=functions.txt Path to a file containing function filenames, one per line.
# This file specifies which function files to process. 
# Example:
#   get_current_weather.sh
#   get_current_weather.js
# @arg funcs*[`_choice_func`] The function filenames
build-declarations-json() {
    set +e
    if [[ "${#argc_funcs[@]}" -gt 0 ]]; then
        names=("${argc_funcs[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file"))
    fi
    if [[ -z "$names" ]]; then
        _die "error: no target functions"
    fi
    json_list=()
    invalid_names=()
    for name in "${names[@]}"; do
        lang="${name##*.}"
        json_data="$("build-single-declaration" "$name")"
        status=$?
        if [ $status -eq 0 ]; then
            json_list+=("$json_data")
        else
            invalid_names+=("$name")
        fi
    done
    if [[ -n "$invalid_names" ]]; then
        _die "error: unable to build declaration for: ${invalid_names[*]}"
    fi
    echo "Build $argc_output"
    echo "["$(IFS=,; echo "${json_list[*]}")"]"  | jq '.' > "$argc_output"
}


# @cmd Build single declaration
# @arg func![`_choice_func`] The function name
build-single-declaration() {
    func="$1"
    lang="${func##*.}"
    cmd="$(_lang_to_cmd "$lang")"
    if [[ "$lang" == sh ]]; then
        argc --argc-export "$lang/$func" | _parse_argc_declaration
    else
        LLM_FUNCTION_DECLARATE=1 "$cmd" "cmd/cmd.$lang" "$func"
    fi
}

# @cmd List functions that can be put into functions.txt
# Examples:
#      argc --list-functions > functions.txt
#      argc --list-functions --write
#      argc --list-functions search_duckduckgo.sh >> functions.txt
# @flag -w --write Output to functions.txt
# @arg funcs*[`_choice_func`] The function filenames, list all available functions if not provided
list-functions() {
    if [[ -n "$argc_write" ]]; then
        _choice_func > functions.txt
        echo "Write functions.txt"
    else
        _choice_func
    fi
}

# @cmd Clean build artifacts
clean() {
    rm -rf functions.json
    rm -rf bin
}

# @cmd Install this repo to aichat functions_dir
install() {
    functions_dir="$(aichat --info | grep functions_dir | awk '{print $2}')"
    if [[ -z "$functions_dir" ]]; then
        _die "error: your aichat version don't support function calling"
    fi
    if [[ ! -e "$functions_dir" ]]; then
        ln -s "$(pwd)" "$functions_dir" 
        echo "$functions_dir symlinked"
    else
        echo "$functions_dir already exists"
    fi
}

# @cmd Show versions of required tools for bug reports.
version() {
    argc --argc-version
    jq --version
    curl --version | head -n 1
}

_parse_argc_declaration() {
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

_lang_to_cmd() {
    match_lang="$1"
    for item in "${LANG_CMDS[@]}"; do
        lang="${item%:*}"
        if [[ "$lang" == "$match_lang" ]]; then
            echo "${item#*:}"
        fi
    done
}

_build_win_sh() {
    cat <<-'EOF'
@echo off
setlocal

set "bin_dir=%~dp0"
for %%i in ("%bin_dir:~0,-1%") do set "script_dir=%%~dpi"
set "script_name=%~n0"
set "script_name=%script_name%.sh"
for /f "delims=" %%a in ('argc --argc-shell-path') do set "_bash_prog=%%a"

"%_bash_prog%" --noprofile --norc "%script_dir%sh\%script_name%" %*
EOF
}

_build_win_lang() {
    lang="$1"
    cmd="$2"
    cat <<-EOF
@echo off
setlocal

set "bin_dir=%~dp0"
for %%i in ("%bin_dir:~0,-1%") do set "script_dir=%%~dpi"
set "script_name=%~n0"

$cmd "%script_dir%cmd\cmd.$lang" "%script_name%.$lang" %*
EOF
}

_choice_func() {
    for item in "${LANG_CMDS[@]}"; do
        lang="${item%:*}"
        ls -1 $lang  | grep "\.$lang$"
    done
}

_choice_func_args() {
    args=( "${argc__positionals[@]}" )
    if [[ "${args[0]}" == *.sh ]]; then
        argc --argc-compgen generic "sh/${args[0]}" "${args[@]}"
    fi
}

_die() {
    echo "$*"
    exit 1
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
