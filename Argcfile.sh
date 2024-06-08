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

# @cmd Run the tool
# @arg cmd![`_choice_bot`] The bot command
# @arg action![`_choice_bot_action`] The bot action
# @arg json The json data
run-bot() {
    if _is_win; then
        ext=".cmd"
    fi
    if [[ -z "$argc_json" ]]; then
        functions_path="bots/$argc_cmd/functions.json"
        if [[ -f "$functions_path" ]]; then
            declaration="$(jq --arg name "$argc_action" '.[] | select(.name == $name)' "$functions_path")"
            if [[ -n "$declaration" ]]; then
                _ask_json_data "$declaration"
            fi
        fi
    fi
    if [[ -z "$argc_json" ]]; then
        _die "error: no JSON data"
    fi
    "$BIN_DIR/$argc_cmd$ext" "$argc_action" "$argc_json"
}

# @cmd Build the project
build() {
    argc build-tools
    argc build-bots
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

# @cmd Build bots
# @option --names-file=bots.txt Path to a file containing bot filenames, one per line.
# Example:
#   hackernews
#   spotify
# @arg bots*[`_choice_bot`] The bot filenames
build-bots() {
    if [[ "${#argc_bots[@]}" -gt 0 ]]; then
        mkdir -p "$TMP_DIR"
        argc_names_file="$TMP_DIR/bots.txt"
        printf "%s\n" "${argc_bots[@]}" > "$argc_names_file"
    else
        argc clean-bots
    fi
    argc build-bots-json --names-file "${argc_names_file}"
    argc build-bots-bin --names-file "${argc_names_file}"
}

# @cmd Build tools to bin
# @option --names-file=bots.txt Path to a file containing bot filenames, one per line.
# @arg bots*[`_choice_bot`] The bot names
build-bots-bin() {
    mkdir -p "$BIN_DIR"
    if [[ "${#argc_bots[@]}" -gt 0 ]]; then
        names=("${argc_bots[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file"))
        if [[ "${#names[@]}" -gt 0 ]]; then
            (cd "$BIN_DIR" && rm -rf "${names[@]}")
        fi
    fi
    if [[ -z "$names" ]]; then
        _die "error: not input bots, not found '$argc_names_file', please create it add some tools."
    fi
    not_found_bots=()
    for name in "${names[@]}"; do
        bot_dir="bots/$name"
        found=false
        for item in "${LANG_CMDS[@]}"; do
            lang="${item%:*}"
            bot_tools_file="$bot_dir/tools.$lang"
            if [[ -f "$bot_tools_file" ]]; then
                found=true
                if _is_win; then
                    bin_file="$BIN_DIR/$name.cmd" 
                    _build_win_shim_bot $lang > "$bin_file"
                else
                    bin_file="$BIN_DIR/$name" 
                    ln -s -f "$PWD/scripts/run-bot.$lang" "$bin_file"
                fi
                echo "Build bot $name"
            fi
        done
        if [[ "$found" = "false" ]]; then
            not_found_bots+=("$name")
        fi
    done
    if [[ -n "$not_found_bots" ]]; then
        _die "error: not found bots: ${not_found_bots[*]}"
    fi
}

# @cmd Build bots functions.json
# @option --names-file=bots.txt Path to a file containing bot filenames, one per line.
# @arg tools*[`_choice_tool`] The tool filenames
build-bots-json() {
    if [[ "${#argc_bots[@]}" -gt 0 ]]; then
        names=("${argc_bots[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file"))
    fi
    if [[ -z "$names" ]]; then
        _die "error: not input bots, not found '$argc_names_file', please create it add some tools."
    fi
    not_found_bots=()
    build_failed_bots=()
    for name in "${names[@]}"; do
        bot_dir="bots/$name"
        build_ok=false
        found=false
        for item in "${LANG_CMDS[@]}"; do
            lang="${item%:*}"
            bot_tools_file="$bot_dir/tools.$lang"
            if [[ -f "$bot_tools_file" ]]; then
                found=true
                json_data="$(build-bot-declarations "$name")" || {
                    build_failed_bots+=("$name")
                }
                declarations_file="$bot_dir/functions.json"
                echo "Build $declarations_file"
                echo "$json_data" > "$declarations_file"
            fi
        done
        if [[ "$found" == "false" ]]; then
            not_found_bots+=("$name")
        fi
    done
    if [[ -n "$not_found_bots" ]]; then
        _die "error: not found bots: ${not_found_bots[*]}"
    fi
    if [[ -n "$build_failed_bots" ]]; then
        _die "error: invalid bots: ${build_failed_bots[*]}"
    fi
}

# @cmd Build function declarations for an bot
# @flag --oneline Summary JSON in one line
# @arg bot![`_choice_bot`] The bot name
build-bot-declarations() {
    tools_path="$(_get_bot_tools_path "$1")"
    if [[ -z "$tools_path" ]]; then
        _die "error: no found entry file at bots/$1/tools.<lang>"
    fi
    lang="${tools_path##*.}"
    cmd="$(_lang_to_cmd "$lang")"
    json="$("$cmd" "scripts/build-declarations.$lang" "$tools_path")"
    if [[ -n "$argc_oneline" ]]; then
        echo "$json" | jq -r '.[] | .name + ": " + (.description | split("\n"))[0]'
    else
        echo "$json"
    fi
}

# @cmd List tools that can be put into functions.txt
# Examples:
#      argc list-tools > tools.txt
list-tools() {
    _choice_tool
}

# @cmd List bots that can be put into bots.txt
# Examples:
#      argc list-bots > bots.txt
list-bots() {
    _choice_bot
}

# @cmd Test the project
test() {
    test-tools
    test-bots
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

# @cmd Test bots
test-bots() {
    tmp_dir="cache/tmp"
    mkdir -p "$tmp_dir"
    names_file="$tmp_dir/bots.txt"
    argc list-bots > "$names_file"
    argc build-bots --names-file "$names_file"
    test-bots-todo-lang
}

# @cmd Test todo-* bots
test-bots-todo-lang() {
    if _is_win; then
        ext=".cmd"
    fi
    test_cases=( \
        'add_todo#{"desc":"Add a todo item"}' \
        'add_todo#{"desc":"Add another todo item"}' \
        'del_todo#{"id":1}' \
        'list_todos#{}' \
        'clear_todos#{}' \
    )
    for item in "${LANG_CMDS[@]}"; do
        cmd="${item#*:}"
        if command -v "$cmd" &> /dev/null; then
            lang="${item%:*}"
            bot_name="todo-$lang"
            rm -rf "cache/$bot_name/todos.json"
            for test_case in "${test_cases[@]}"; do
                IFS='#' read -r action data <<<"${test_case}"
                cmd_path="$BIN_DIR/$bot_name$ext"
                echo "Test $cmd_path: "
                "$cmd_path" "$action" "$data"
            done
        fi
    done

}

# @cmd Clean tools
clean-tools() {
    _choice_tool | sed 's/\.\([a-z]\+\)$//' |  xargs -I{} rm -rf "$BIN_DIR/{}"
    rm -rf functions.json
}

# @cmd Clean bots
clean-bots() {
    _choice_bot | xargs -I{} rm -rf "$BIN_DIR/{}" 
    _choice_bot | xargs -I{} rm -rf bots/{}/functions.json
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

_get_bot_tools_path() {
    name="$1"
    for item in "${LANG_CMDS[@]}"; do
        lang="${item%:*}"
        entry_file="bots/$name/tools.$lang"
        if [[ -f "bots/$name/tools.$lang" ]]; then
            echo "$entry_file"
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
    echo "$declaration" | _inspect_declaration_params | sed 's/^/>  /'
    echo 'Generate placeholder data:'
    data="$(echo "$declaration" | _generate_data_according_declaration)"
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

_inspect_declaration_params() {
    jq -r '
def get_indicator:
    .value.type as $type |
    [
        { condition: ($type == "array" and .required), result: "+" },
        { condition: ($type == "array"), result: "*" },
        { condition: .required, result: "!" },
        { condition: true, result: "" }
    ] | map(select(.condition) | .result) | first;

def get_kind:
    .value.type as $type |
    (.value.enum // []) as $enum |
    ([
        { condition: ($type == "array"), result: "string[]" },
        { condition: ($type == "string" and ($enum | length > 0)), result: ($enum | join("|")) },
        { condition: ($type == "string"), result: "" },
        { condition: true, result: $type }
    ] | map(select(.condition) | .result) | first) as $kind |
    if $kind != "" then "(\($kind))" else "" end;

def print_property:
    .key as $key |
    (.value.description | split("\n")[0]) as $description |
    (. | get_kind) as $kind |
    (. | get_indicator) as $indicator |
    "\($key)\($kind)\($indicator): \($description)";

.parameters | 
.required as $requiredProperties |
.properties | to_entries[] | 
.key as $key | .+ { "required": ($requiredProperties | index($key) != null) } |
print_property
'
}

_generate_data_according_declaration() {
    jq -c '
def convert_string:
    if has("enum") then .enum[0] else "foo" end;

def convert_property:
    .key as $key |
    .value.type as $type |
    [
        { condition: ($type == "string"), result: { $key: (.value | convert_string) }},
        { condition: ($type == "boolean"), result: { $key: false }},
        { condition: ($type == "integer"), result: { $key: 42 }},
        { condition: ($type == "number"), result: { $key: 3.14 }},
        { condition: ($type == "array"), result: { $key: [ "v1" ] } }
    ] | map(select(.condition) | .result) | first;

.parameters |
[
    .properties | to_entries[] | convert_property
] | add // {}
'
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

_choice_bot() {
    ls -1 bots
}

_choice_bot_action() {
    if [[ "$ARGC_COMPGEN" -eq 1 ]]; then
        expr="s/: /\t/"
    else
        expr="s/:.*//"
    fi
    argc build-bot-declarations "$1" --oneline  | sed "$expr"
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
