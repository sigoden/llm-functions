#!/usr/bin/env bash
set -e

# @meta dotenv

BIN_DIR=bin

LANG_CMDS=( \
    "sh:bash" \
    "js:node" \
    "py:python" \
)

# @cmd Call the function
# @arg cmd![`_choice_cmd`] The function command
# @arg json The json data
call() {
    if _is_win; then
        ext=".cmd"
    fi
    "$BIN_DIR/$argc_cmd$ext" "$argc_json"
}

# @cmd Build the project
# @option --names-file=functions.txt Path to a file containing function filenames, one per line.
# This file specifies which function files to build. 
# Example:
#   get_current_weather.sh
#   may_execute_js_code.js
build() {
    argc build-declarations-json --names-file "${argc_names_file}"
    argc build-bin --names-file "${argc_names_file}"
}

# @cmd Build bin dir 
# @option --names-file=functions.txt Path to a file containing function filenames, one per line.
build-bin() {
    if [[ ! -f "$argc_names_file" ]]; then
        _die "no found "$argc_names_file""
    fi
    mkdir -p "$BIN_DIR"
    rm -rf "$BIN_DIR"/*
    names=($(cat "$argc_names_file"))
    not_found_funcs=()
    for name in "${names[@]}"; do
        basename="${name%.*}"
        lang="${name##*.}"
        func_file="tools/$name"
        if [[  -f "$func_file" ]]; then
            if _is_win; then
                bin_file="$BIN_DIR/$basename.cmd" 
                _build_win_shim $lang > "$bin_file"
            else
                bin_file="$BIN_DIR/$basename" 
                ln -s "$PWD/scripts/run-tool.$lang" "$bin_file"
            fi
        else
            not_found_funcs+=("$name")
        fi
    done
    if [[ -n "$not_found_funcs" ]]; then
        _die "error: not founds functions: ${not_found_funcs[*]}"
    fi
    for name in "$BIN_DIR"/*; do
        echo "Build $name"
    done
}

# @cmd Build declarations.json
# @option --output=functions.json <FILE> Path to a json file to save function declarations
# @option --names-file=functions.txt Path to a file containing function filenames, one per line.
# @arg funcs*[`_choice_func`] The function filenames
build-declarations-json() {
    if [[ "${#argc_funcs[@]}" -gt 0 ]]; then
        names=("${argc_funcs[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file"))
    fi
    if [[ -z "$names" ]]; then
        _die "error: no function for building declarations.json"
    fi
    json_list=()
    not_found_funcs=()
    build_failed_funcs=()
    for name in "${names[@]}"; do
        lang="${name##*.}"
        func_file="tools/$name"
        if [[ ! -f "$func_file" ]]; then
            not_found_funcs+=("$name")
            continue;
        fi
        json_data="$("build-single-declaration" "$name")" || {
            build_failed_funcs+=("$name")
        }
        json_list+=("$json_data")
    done
    if [[ -n "$not_found_funcs" ]]; then
        _die "error: not found functions: ${not_found_funcs[*]}"
    fi
    if [[ -n "$build_failed_funcs" ]]; then
        _die "error: invalid functions: ${build_failed_funcs[*]}"
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
    LLM_FUNCTION_ACTION=declarate "$cmd" "scripts/run-tool.$lang" "$func"
}

# @cmd List functions that can be put into functions.txt
# Examples:
#      argc --list-functions > functions.txt
#      argc --list-functions search_duckduckgo.sh >> functions.txt
# @arg funcs*[`_choice_func`] The function filenames, list all available functions if not provided
list-functions() {
    _choice_func
}

# @cmd Test the project
test() {
    func_names_file=functions.txt.test
    argc list-functions > "$func_names_file"
    argc build --names-file "$func_names_file"
    argc test-functions
    rm -rf "$func_names_file"
}

# @cmd Test call functions
test-functions() {
    if _is_win; then
        ext=".cmd"
    fi
    test_cases=( \
        'sh#may_execute_command#{"command":"echo \"✓\""}' \
        'js#may_execute_js_code#{"code":"console.log(\"✓\")"}' \
        'py#may_execute_py_code#{"code":"print(\"✓\")"}' \
    )

    for test_case in "${test_cases[@]}"; do
        IFS='#' read -r lang func data <<<"${test_case}"
        cmd="$(_lang_to_cmd "$lang")"
        cmd_path="$BIN_DIR/$func$ext"
        if command -v "$cmd" &> /dev/null; then
            echo -n "Test $cmd_path: "
            "$cmd_path" "$data"
            if ! _is_win; then
                echo -n "Test $cmd scripts/run-tool.$lang $func: "
                "$cmd" "scripts/run-tool.$lang" "$func" "$data"
            fi
        fi
    done
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

# @cmd Create a boilplate tool script file.
# @arg args~
create() {
    ./scripts/create-tool.sh "$@"
}

# @cmd Show pre-requisite tool versions
version() {
    uname -a
    argc --argc-version
    jq --version
    for item in "${LANG_CMDS[@]}"; do
        cmd="${item#*:}"
        if [[ "$cmd" == "bash" ]]; then
            echo "$(argc --argc-shell-path) $("$(argc --argc-shell-path)" --version | head -n 1)"
        elif command -v "$cmd" &> /dev/null; then
            echo "$(_normalize_path "$(which $cmd)") $($cmd --version)"
        fi
    done
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

_build_win_shim() {
    lang="$1"
    cmd="$(_lang_to_cmd "$lang")"
    if [[ "$lang" == "sh" ]]; then
        run="\"$(argc --argc-shell-path)\" --noprofile --norc"
    else
        run="\"$(_normalize_path "$(which $cmd)")\""
    fi
    cat <<-EOF
@echo off
setlocal

set "bin_dir=%~dp0"
for %%i in ("%bin_dir:~0,-1%") do set "script_dir=%%~dpi"
set "script_name=%~n0"

$run "%script_dir%scripts\run-tool.$lang" "%script_name%.$lang" %*
EOF
}

_normalize_path() {
    if _is_win; then
        cygpath -w "$1"
    else
        echo "$1"
    fi
}

_is_win() {
    if [[ "$OS" == "Windows_NT" ]]; then
        return 0
    else
        return 1
    fi
}

_choice_func() {
    for item in "${LANG_CMDS[@]}"; do
        lang="${item%:*}"
        cmd="${item#*:}"
        if command -v "$cmd" &> /dev/null; then
            ls -1 tools | grep "\.$lang$"
        fi
    done
}

_choice_cmd() {
    ls -1 "$BIN_DIR" | sed -e 's/\.cmd$//'
}

_die() {
    echo "$*"
    exit 1
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
