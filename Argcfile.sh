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
# @alias tool:run
# @arg cmd![`_choice_cmd`] The tool command
# @arg json The json data
run@tool() {
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

# @cmd Run the agent
# @alias agent:run
# @arg cmd![`_choice_agent`] The agent command
# @arg action![`_choice_agent_action`] The agent action
# @arg json The json data
run@agent() {
    if _is_win; then
        ext=".cmd"
    fi
    if [[ -z "$argc_json" ]]; then
        functions_path="agents/$argc_cmd/functions.json"
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
    if [[ -f tools.txt ]]; then
        argc build@tool
    else
        echo 'Skipped building tools sine tools.txt is missing'
    fi
    if [[ -f agents.txt ]]; then
        argc build@agent
    else
        echo 'Skipped building agents sine agents.txt is missing'
    fi
}

# @cmd Build tools
# @alias tool:build
# @option --names-file=tools.txt Path to a file containing tool filenames, one per line.
# This file specifies which tools will be used.
# @option --declarations-file=functions.json <FILE> Path to a json file to save function declarations
# Example:
#   get_current_weather.sh
#   may_execute_js_code.js
# @arg tools*[`_choice_tool`] The tool filenames
build@tool() {
    if [[ "${#argc_tools[@]}" -gt 0 ]]; then
        mkdir -p "$TMP_DIR"
        argc_names_file="$TMP_DIR/tools.txt"
        printf "%s\n" "${argc_tools[@]}" > "$argc_names_file"
    elif [[ "$argc_declarations_file" == "functions.json" ]]; then
        argc clean@tool
    fi
    argc build-declarations@tool --names-file "${argc_names_file}" --declarations-file "${argc_declarations_file}"
    argc build-bin@tool --names-file "${argc_names_file}"
}

# @cmd Build tools to bin
# @alias tool:build-bin
# @option --names-file=tools.txt Path to a file containing tool filenames, one per line.
# @arg tools*[`_choice_tool`] The tool filenames
build-bin@tool() {
    mkdir -p "$BIN_DIR"
    if [[ "${#argc_tools[@]}" -gt 0 ]]; then
        names=("${argc_tools[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file" | grep -v '^#'))
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
                _build_win_shim tool $lang > "$bin_file"
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

# @cmd Build tools function declarations file
# @alias tool:build-declarations
# @option --names-file=tools.txt Path to a file containing tool filenames, one per line.
# @option --declarations-file=functions.json <FILE> Path to a json file to save function declarations
# @arg tools*[`_choice_tool`] The tool filenames
build-declarations@tool() {
    if [[ "${#argc_tools[@]}" -gt 0 ]]; then
        names=("${argc_tools[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file" | grep -v '^#'))
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
        json_data="$(generate-declarations@tool "$name" | jq -r '.[0]')" || {
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


# @cmd Generate function declaration for the tool
# @alias tool:generate-declarations
# @arg tool![`_choice_tool`] The function name
generate-declarations@tool() {
    lang="${1##*.}"
    cmd="$(_lang_to_cmd "$lang")"
    "$cmd" "scripts/build-declarations.$lang" "tools/$1"
}

# @cmd Build agents
# @alias agent:build
# @option --names-file=agents.txt Path to a file containing agent filenames, one per line.
# Example:
#   hackernews
#   spotify
# @arg agents*[`_choice_agent`] The agent filenames
build@agent() {
    if [[ "${#argc_agents[@]}" -gt 0 ]]; then
        mkdir -p "$TMP_DIR"
        argc_names_file="$TMP_DIR/agents.txt"
        printf "%s\n" "${argc_agents[@]}" > "$argc_names_file"
    else
        argc clean@agent
    fi
    argc build-declarations@agent --names-file "${argc_names_file}"
    argc build-bin@agent --names-file "${argc_names_file}"
}

# @cmd Build agents to bin
# @alias agent:build-bin
# @option --names-file=agents.txt Path to a file containing agent dirs, one per line.
# @arg agents*[`_choice_agent`] The agent names
build-bin@agent() {
    mkdir -p "$BIN_DIR"
    if [[ "${#argc_agents[@]}" -gt 0 ]]; then
        names=("${argc_agents[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file" | grep -v '^#'))
        if [[ "${#names[@]}" -gt 0 ]]; then
            (cd "$BIN_DIR" && rm -rf "${names[@]}")
        fi
    fi
    if [[ -z "$names" ]]; then
        _die "error: not input agents, not found '$argc_names_file', please create it add some tools."
    fi
    not_found_agents=()
    for name in "${names[@]}"; do
        agent_dir="agents/$name"
        found=false
        for item in "${LANG_CMDS[@]}"; do
            lang="${item%:*}"
            agent_tools_file="$agent_dir/tools.$lang"
            if [[ -f "$agent_tools_file" ]]; then
                found=true
                if _is_win; then
                    bin_file="$BIN_DIR/$name.cmd" 
                    _build_win_shim agent $lang > "$bin_file"
                else
                    bin_file="$BIN_DIR/$name" 
                    ln -s -f "$PWD/scripts/run-agent.$lang" "$bin_file"
                fi
                echo "Build agent $name"
            fi
        done
        if [[ "$found" = "false" ]]; then
            not_found_agents+=("$name")
        fi
    done
    if [[ -n "$not_found_agents" ]]; then
        _die "error: not found agents: ${not_found_agents[*]}"
    fi
}

# @cmd Build agents function declarations file
# @alias agent:build-declarations
# @option --names-file=agents.txt Path to a file containing agent dirs, one per line.
# @arg agents*[`_choice_agent`] The tool filenames
build-declarations@agent() {
    if [[ "${#argc_agents[@]}" -gt 0 ]]; then
        names=("${argc_agents[@]}" )
    elif [[ -f "$argc_names_file" ]]; then
        names=($(cat "$argc_names_file" | grep -v '^#'))
    fi
    if [[ -z "$names" ]]; then
        _die "error: not input agents, not found '$argc_names_file', please create it add some tools."
    fi
    not_found_agents=()
    build_failed_agents=()
    for name in "${names[@]}"; do
        agent_dir="agents/$name"
        build_ok=false
        found=false
        for item in "${LANG_CMDS[@]}"; do
            lang="${item%:*}"
            agent_tools_file="$agent_dir/tools.$lang"
            if [[ -f "$agent_tools_file" ]]; then
                found=true
                json_data="$(generate-declarations@agent "$name")" || {
                    build_failed_agents+=("$name")
                }
                declarations_file="$agent_dir/functions.json"
                echo "Build $declarations_file"
                echo "$json_data" > "$declarations_file"
            fi
        done
        if [[ "$found" == "false" ]]; then
            not_found_agents+=("$name")
        fi
    done
    if [[ -n "$not_found_agents" ]]; then
        _die "error: not found agents: ${not_found_agents[*]}"
    fi
    if [[ -n "$build_failed_agents" ]]; then
        _die "error: invalid agents: ${build_failed_agents[*]}"
    fi
}

# @cmd Generate function declarations for the agent
# @alias agent:generate-declarations
# @flag --oneline Summary JSON in one line
# @arg agent![`_choice_agent`] The agent name
generate-declarations@agent() {
    tools_path="$(_get_agent_tools_path "$1")"
    if [[ -z "$tools_path" ]]; then
        _die "error: no found entry file at agents/$1/tools.<lang>"
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

# @cmd List tools which can be put into functions.txt
# @alias tool:list
# Examples:
#      argc list-tools > tools.txt
list@tool() {
    _choice_tool
}

# @cmd List agents which can be put into agents.txt
# @alias agent:list
# Examples:
#      argc list-agents > agents.txt
list@agent() {
    _choice_agent
}

# @cmd Test the project
test() {
    test@tool
    test@agent
}

# @cmd Test tools
# @alias tool:test
test@tool() {
    mkdir -p "$TMP_DIR"
    names_file="$TMP_DIR/tools.txt"
    declarations_file="$TMP_DIR/functions.json"
    argc list@tool > "$names_file"
    argc build@tool --names-file "$names_file" --declarations-file "$declarations_file"
    test-execute-code-tools
}

# @cmd Test maybe_execute_* tools
# @alias tool:test-execute-code
test-execute-code-tools() {
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
# @alias tool:test-demo
test-demo-tools() {
    for item in "${LANG_CMDS[@]}"; do
        lang="${item%:*}"
        echo "---- Test demo_tool.$lang ---"
        argc build-bin@tool "demo_tool.$lang"
        argc run@tool demo_tool '{
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

# @cmd Test agents
# @alias agent:test
test@agent() {
    tmp_dir="cache/tmp"
    mkdir -p "$tmp_dir"
    names_file="$tmp_dir/agents.txt"
    argc list@agent > "$names_file"
    argc build@agent --names-file "$names_file"
    test-todo-agents
}

# @cmd Test todo-* agents
# @alias agent:test-todo
test-todo-agents() {
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
            agent_name="todo-$lang"
            rm -rf "cache/$agent_name/todos.json"
            for test_case in "${test_cases[@]}"; do
                IFS='#' read -r action data <<<"${test_case}"
                cmd_path="$BIN_DIR/$agent_name$ext"
                echo "Test $cmd_path: "
                "$cmd_path" "$action" "$data"
            done
        fi
    done

}

# @cmd Clean tools
# @alias tool:clean
clean@tool() {
    _choice_tool | sed 's/\.\([a-z]\+\)$//' |  xargs -I{} rm -rf "$BIN_DIR/{}"
    rm -rf functions.json
}

# @cmd Clean agents
# @alias agent:clean
clean@agent() {
    _choice_agent | xargs -I{} rm -rf "$BIN_DIR/{}" 
    _choice_agent | xargs -I{} rm -rf agents/{}/functions.json
}

# @cmd Install this repo to aichat functions_dir
install() {
    functions_dir="$(aichat --info | grep -w functions_dir | awk '{print $2}')"
    if [[ -z "$functions_dir" ]]; then
        _die "error: your aichat version don't support function calling"
    fi
    if [[ ! -e "$functions_dir" ]]; then
        if _is_win; then
            current_dir="$(cygpath -w "$(pwd)")"
            cmd <<< "mklink /D \"${functions_dir%/}\" \"${current_dir%/}\"" > /dev/null
        else
            ln -s "$(pwd)" "$functions_dir" 
        fi
        echo "$functions_dir symlinked"
    else
        echo "$functions_dir already exists"
    fi
}

# @cmd Create a boilplate tool script
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

_get_agent_tools_path() {
    name="$1"
    for item in "${LANG_CMDS[@]}"; do
        lang="${item%:*}"
        entry_file="agents/$name/tools.$lang"
        if [[ -f "agents/$name/tools.$lang" ]]; then
            echo "$entry_file"
        fi
    done
}

_build_win_shim() {
    kind="$1"
    lang="$2"
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

$run "%script_dir%scripts\run-$kind.$lang" "%script_name%" %*
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

_choice_agent() {
    ls -1 agents
}

_choice_agent_action() {
    if [[ "$ARGC_COMPGEN" -eq 1 ]]; then
        expr="s/: /\t/"
    else
        expr="s/:.*//"
    fi
    argc generate-declarations@agent "$1" --oneline  | sed "$expr"
}

_choice_cmd() {
    ls -1 "$BIN_DIR" | sed -e 's/\.cmd$//'
}

_die() {
    echo "$*" >&2
    exit 1
}

if _is_win; then set -o igncr; fi

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
