#!/usr/bin/env bash
set -e

# @meta dotenv

BIN_DIR=bin
TMP_DIR="cache/tmp"

LANG_CMDS=( \
    "sh:bash" \
    "js:node" \
    "py:python" \
)

# @cmd Run the tool
# @alias call
# @arg cmd![`_choice_cmd`] The tool command
# @arg json The json data
run-tool() {
    if _is_win; then
        ext=".cmd"
    fi
    if [[ -z "$argc_json" ]]; then
        declaration="$(jq --arg name "$argc_cmd" '.[] | select(.name == $name)' functions.json)"
        if [[ -n "$declaration" ]]; then
            _ask_json_data "$declaration"
        fi
    fi
    if [[ -z "$argc_json" ]]; then
        _die "error: no JSON data"
    fi
    "$BIN_DIR/$argc_cmd$ext" "$argc_json"
}

# @cmd Build the project
build() {
    argc build-tools
}

# @cmd Build tools
# @option --names-file=tools.txt Path to a file containing tool filenames, one per line.
# This file specifies which tools will be used.
# @option --declarations-file=functions.json <FILE> Path to a json file to save function declarations
# Example:
#   get_current_weather.sh
#   may_execute_js_code.js
# @arg tools*[`_choice_tool`] The tool filenames
build-tools() {
    if [[ "${#argc_tools[@]}" -gt 0 ]]; then
        mkdir -p "$TMP_DIR"
        argc_names_file="$TMP_DIR/tools.txt"
        printf "%s\n" "${argc_tools[@]}" > "$argc_names_file"
    else
        argc clean-tools
    fi
    argc build-tools-json --names-file "${argc_names_file}" --declarations-file "${argc_declarations_file}"
    argc build-tools-bin --names-file "${argc_names_file}"
}

# @cmd Build tools to bin
# @option --names-file=tools.txt Path to a file containing tool filenames, one per line.
# @arg tools*[`_choice_tool`] The tool filenames
build-tools-bin() {
    mkdir -p "$BIN_DIR"
    if [[ "${#argc_tools[@]}" -gt 0 ]]; then
        names=("${argc_tools[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file"))
        if [[ "${#names[@]}" -gt 0 ]]; then
            (cd "$BIN_DIR" && rm -rf "${names[@]}")
        fi
    fi
    if [[ -z "$names" ]]; then
        _die "error: not input tools, not found '$argc_names_file', please create it add some tools."
    fi
    not_found_tools=()
    for name in "${names[@]}"; do
        basename="${name%.*}"
        lang="${name##*.}"
        tool_path="tools/$name"
        if [[  -f "$tool_path" ]]; then
            if _is_win; then
                bin_file="$BIN_DIR/$basename.cmd" 
                _build_win_shim_tool $lang > "$bin_file"
            else
                bin_file="$BIN_DIR/$basename" 
                ln -s -f "$PWD/scripts/run-tool.$lang" "$bin_file"
            fi
            echo "Build tool $name"
        else
            not_found_tools+=("$name")
        fi
    done
    if [[ -n "$not_found_tools" ]]; then
        _die "error: not found tools: ${not_found_tools[*]}"
    fi
}

# @cmd Build tool functions.json
# @option --names-file=tools.txt Path to a file containing tool filenames, one per line.
# @option --declarations-file=functions.json <FILE> Path to a json file to save function declarations
# @arg tools*[`_choice_tool`] The tool filenames
build-tools-json() {
    if [[ "${#argc_tools[@]}" -gt 0 ]]; then
        names=("${argc_tools[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file"))
    fi
    if [[ -z "$names" ]]; then
        _die "error: not input tools, not found '$argc_names_file', please create it add some tools."
    fi
    json_list=()
    not_found_tools=()
    build_failed_tools=()
    for name in "${names[@]}"; do
        lang="${name##*.}"
        tool_path="tools/$name"
        if [[ ! -f "$tool_path" ]]; then
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

# @cmd List tools that can be put into tools.txt
# Examples:
#      argc list-tools > tools.txt
list-tools() {
    _choice_tool
}

# @cmd Test the project
test() {
    test-tools
}

# @cmd Test tools
test-tools() {
    mkdir -p "$TMP_DIR"
    names_file="$TMP_DIR/tools.txt"
    declarations_file="$TMP_DIR/functions.json"
    argc list-tools > "$names_file"
    argc build-tools --names-file "$names_file" --declarations-file "$declarations_file"
    test-tools-execute-lang
}

# @cmd Test maybe_execute_* tools
test-tools-execute-lang() {
    if _is_win; then
        ext=".cmd"
    fi
    test_cases=( \
        'sh#may_execute_command#{"command":"echo \"✓\""}' \
        'js#may_execute_js_code#{"code":"console.log(\"✓\")"}' \
        'py#may_execute_py_code#{"code":"print(\"✓\")"}' \
    )

    for test_case in "${test_cases[@]}"; do
        IFS='#' read -r lang tool_name data <<<"${test_case}"
        cmd="$(_lang_to_cmd "$lang")"
        if command -v "$cmd" &> /dev/null; then
            cmd_path="$BIN_DIR/$tool_name$ext"
            echo -n "Test $cmd_path: "
            "$cmd_path" "$data"
            if ! _is_win; then
                echo -n "Test $cmd scripts/run-tool.$lang $tool_name: "
                "$cmd" "scripts/run-tool.$lang" "$tool_name" "$data"
            fi
        fi
    done
}

# @cmd Test demo tools
test-tools-demo() {
    for item in "${LANG_CMDS[@]}"; do
        lang="${item%:*}"
        echo "---- Test demo_tool.$lang ---"
        argc build-tools-bin "demo_tool.$lang"
        argc run-tool demo_tool '{
     "boolean": true,
     "string": "Hello",
     "string_enum": "foo",
     "integer": 123,
     "number": 3.14,
     "array": [
          "a",
          "b",
          "c"
     ],
     "string_optional": "OptionalValue",
     "array_optional": [
          "x",
          "y"
     ]
}'
        echo
    done
}


# @cmd Clean tools
clean-tools() {
    _choice_tool | sed 's/\.\([a-z]\+\)$//' |  xargs -I{} rm -rf "$BIN_DIR/{}"
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

_build_win_shim_tool() {
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

_ask_json_data() {
    declaration="$1"
    echo 'Missing the JSON data but here are its properties:'
    echo "$declaration" | ./scripts/declarations-util.sh pretty-print | sed -n '2,$s/^/>/p'
    echo 'Generate placeholder data:'
    data="$(echo "$declaration" | _declarations_json_data)"
    echo ">  $data"
    read -r -p 'Use the generated data? (y/n) ' res
    case "$res" in
    [yY][eE][sS]|[yY])
        argc_json="$data"
        ;;
    *)
        read -r -p "Please enter the data: " data
        argc_json="$data"
        ;;
    esac
}

_declarations_json_data() {
   ./scripts/declarations-util.sh generate-json | tail -n +2
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
    echo "$*" >&2
    exit 1
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
