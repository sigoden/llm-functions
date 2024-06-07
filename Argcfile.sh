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
# @option --names-file=functions.txt Path to a file containing tool filenames, one per line.
# @option --declarations-file=functions.json <FILE> Path to a json file to save function declarations
# This file specifies which function files to build. 
# Example:
#   get_current_weather.sh
#   may_execute_js_code.js
build() {
    argc build-declarations-json --names-file "${argc_names_file}" --declarations-file "${argc_declarations_file}"
    argc build-bin --names-file "${argc_names_file}"
}

# @cmd Build tool binaries
# @option --names-file=functions.txt Path to a file containing tool filenames, one per line.
# @arg tools*[`_choice_tool`] The tool filenames
build-bin() {
    if [[ "${#argc_tools[@]}" -gt 0 ]]; then
        names=("${argc_tools[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file"))
    fi
    if [[ -z "$names" ]]; then
        _die "error: no tools selected"
    fi
    mkdir -p "$BIN_DIR"
    rm -rf "$BIN_DIR"/*
    not_found_tools=()
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
                ln -s -f "$PWD/scripts/run-tool.$lang" "$bin_file"
            fi
        else
            not_found_tools+=("$name")
        fi
    done
    if [[ -n "$not_found_tools" ]]; then
        _die "error: not found tools: ${not_found_tools[*]}"
    fi
    for name in "$BIN_DIR"/*; do
        echo "Build $name"
    done
}

# @cmd Build declarations.json
# @option --names-file=functions.txt Path to a file containing tool filenames, one per line.
# @option --declarations-file=functions.json <FILE> Path to a json file to save function declarations
# @arg tools*[`_choice_tool`] The tool filenames
build-declarations-json() {
    if [[ "${#argc_tools[@]}" -gt 0 ]]; then
        names=("${argc_tools[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file"))
    fi
    if [[ -z "$names" ]]; then
        _die "error: no tools selected"
    fi
    json_list=()
    not_found_tools=()
    build_failed_tools=()
    for name in "${names[@]}"; do
        lang="${name##*.}"
        func_file="tools/$name"
        if [[ ! -f "$func_file" ]]; then
            not_found_tools+=("$name")
            continue;
        fi
        json_data="$(build-tool-declaration "$name")" || {
            build_failed_tools+=("$name")
        }
        json_list+=("$json_data")
    done
    if [[ -n "$not_found_tools" ]]; then
        _die "error: not found tools: ${not_found_tools[*]}"
    fi
    if [[ -n "$build_failed_tools" ]]; then
        _die "error: invalid tools: ${build_failed_tools[*]}"
    fi
    echo "Build $argc_declarations_file"
    echo "["$(IFS=,; echo "${json_list[*]}")"]"  | jq '.' > "$argc_declarations_file"
}


# @cmd Build function declaration for a tool
# @arg tool![`_choice_tool`] The function name
build-tool-declaration() {
    lang="${1##*.}"
    cmd="$(_lang_to_cmd "$lang")"
    "$cmd" "scripts/build-declarations.$lang" "tools/$1" | jq '.[0]'
}

# @cmd List tools that can be put into functions.txt
# Examples:
#      argc list-tools > functions.txt
list-tools() {
    _choice_tool
}

# @cmd Test the project
test() {
    mkdir -p tmp/tests
    names_file=tmp/tests/functions.txt
    declarations_file=tmp/tests/functions.json
    argc list-tools > "$names_file"
    argc build --names-file "$names_file" --declarations-file "$declarations_file"
    argc test-tools
}

# @cmd Test call functions
test-tools() {
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

# @cmd Create a boilplate tool scriptfile.
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

_choice_tool() {
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
