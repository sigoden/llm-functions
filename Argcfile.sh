#!/usr/bin/env bash
set -e

BIN_DIR=bin
TMP_DIR="cache/__tmp__"
VENV_DIR=".venv"

LANG_CMDS=( \
    "sh:bash" \
    "js:node" \
    "py:python" \
)

# @cmd Run the tool
# @option -C --cwd <dir> Change the current working directory
# @alias tool:run
# @arg tool![`_choice_tool`] The tool name
# @arg json The json data
run@tool() {
    if [[ -z "$argc_json" ]]; then
        declaration="$(generate-declarations@tool "$argc_tool" | jq -r '.[0]')"
        if [[ -n "$declaration" ]]; then
            _ask_json_data "$declaration"
        fi
    fi
    if [[ -z "$argc_json" ]]; then
        _die "error: no JSON data"
    fi
    lang="${argc_tool##*.}"
    cmd="$(_lang_to_cmd "$lang")"
    run_tool_script="scripts/run-tool.$lang"
    [[ -n "$argc_cwd" ]] && cd "$argc_cwd"
    exec "$cmd" "$run_tool_script" "$argc_tool" "$argc_json"
}

# @cmd Run the agent
# @alias agent:run
# @option -C --cwd <dir> Change the current working directory
# @arg agent![`_choice_agent`] The agent name
# @arg action![?`_choice_agent_action`] The agent action
# @arg json The json data
run@agent() {
    if [[ -z "$argc_json" ]]; then
        declaration="$(generate-declarations@agent "$argc_agent" | jq --arg name "$argc_action" '.[] | select(.name == $name)')"
        if [[ -n "$declaration" ]]; then
            _ask_json_data "$declaration"
        fi
    fi
    if [[ -z "$argc_json" ]]; then
        _die "error: no JSON data"
    fi
    tools_path="$(_get_agent_tools_path "$argc_agent")"
    lang="${tools_path##*.}"
    cmd="$(_lang_to_cmd "$lang")"
    run_agent_script="scripts/run-agent.$lang"
    [[ -n "$argc_cwd" ]] && cd "$argc_cwd"
    exec "$cmd" "$run_agent_script"  "$argc_agent" "$argc_action" "$argc_json"
}

# @cmd Build the project
build() {
    if [[ -f tools.txt ]]; then
        argc build@tool
    else
        echo 'Skipped building tools since tools.txt is missing'
    fi
    if [[ -f agents.txt ]]; then
        argc build@agent
    else
        echo 'Skipped building agents since agents.txt is missing'
    fi
}

# @cmd Build tools
# @alias tool:build
# @option --names-file=tools.txt Path to a file containing tool filenames, one per line.
# This file specifies which tools will be used.
# @option --declarations-file=functions.json <FILE> Path to a json file to save function declarations
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
        _die "error: no tools provided. '$argc_names_file' is missing. please create it and add some tools."
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
                if [[ "$lang" == "py" && -d "$VENV_DIR" ]]; then
                    rm -rf "$bin_file"
                    _build_py_shim tool $lang > "$bin_file"
                    chmod +x "$bin_file"
                else
                    ln -s -f "$PWD/scripts/run-tool.$lang" "$bin_file"
                fi
            fi
            echo "Build bin/$basename"
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
        _die "error: no tools provided. '$argc_names_file' is missing. please create it and add some tools."
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
        if [[ "$json_data" == "null" ]]; then
            _die "error: failed to build declarations for tool $name"
        fi
        json_list+=("$json_data")
    done
    if [[ -n "$not_found_tools" ]]; then
        _die "error: not found tools: ${not_found_tools[*]}"
    fi
    if [[ -n "$build_failed_tools" ]]; then
        _die "error: invalid tools: ${build_failed_tools[*]}"
    fi
    json_data="$(echo "${json_list[@]}" | jq -s '.')"
    if [[ "$argc_declarations_file" == "-" ]]; then
        echo "$json_data"
    else
        echo "Build $argc_declarations_file"
        echo "$json_data" > "$argc_declarations_file"
    fi
}


# @cmd Generate function declaration for the tool
# @alias tool:generate-declarations
# @arg tool![`_choice_tool`] The tool name
generate-declarations@tool() {
    lang="${1##*.}"
    cmd="$(_lang_to_cmd "$lang")"
    "$cmd" "scripts/build-declarations.$lang" "tools/$1"
}

# @cmd Build agents
# @alias agent:build
# @option --names-file=agents.txt Path to a file containing agent filenames, one per line.
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
        _die "error: no agents provided. '$argc_names_file' is missing. please create it and add some agents."
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
                    if [[ "$lang" == "py" && -d "$VENV_DIR" ]]; then
                        rm -rf "$bin_file"
                        _build_py_shim tool $lang > "$bin_file"
                        chmod +x "$bin_file"
                    else
                        ln -s -f "$PWD/scripts/run-agent.$lang" "$bin_file"
                    fi
                fi
                echo "Build bin/$name"
                tool_names_file="$agent_dir/tools.txt"
                if [[ -f "$tool_names_file" ]]; then
                    argc build-bin@tool --names-file "${tool_names_file}"
                fi
                break
            fi
        done
        if [[ "$found" == "false" ]] && [[ ! -d "$agent_dir"  ]]; then
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
        _die "error: no agents provided. '$argc_names_file' is missing. please create it and add some agents."
    fi
    not_found_agents=()
    build_failed_agents=()
    exist_tools="$(ls -1 tools)"
    for name in "${names[@]}"; do
        agent_dir="agents/$name"
        declarations_file="$agent_dir/functions.json"
        tool_names_file="$agent_dir/tools.txt"
        found=false
        if [[ -d "$agent_dir" ]]; then
            found=true
            ok=true
            json_data=""
            agent_json_data=""
            tools_json_data=""
            for item in "${LANG_CMDS[@]}"; do
                lang="${item%:*}"
                agent_tools_file="$agent_dir/tools.$lang"
                if [[ -f "$agent_tools_file" ]]; then
                    agent_json_data="$(generate-declarations@agent "$name")" || {
                        ok=false
                        build_failed_agents+=("$name")
                    }
                    break
                fi
            done
            if [[ -f "$tool_names_file" ]]; then
                if grep -q '^web_search\.' "$tool_names_file" && ! grep -q '^web_search\.' <<<"$exist_tools"; then
                    echo "WARNING: no found web_search tool, please run \`argc link-web-search <web-search-tool>\` to set one."
                fi
                if grep -q '^code_interpreter\.' "$tool_names_file" && ! grep -q '^code_interpreter\.' <<<"$exist_tools"; then
                    echo "WARNING: no found code_interpreter tool, please run \`argc link-code-interpreter <execute-code-tool>\` to set one."
                fi
                tools_json_data="$(argc build-declarations@tool --names-file="$tool_names_file" --declarations-file=-)" || {
                    ok=false
                    build_failed_agents+=("$name")
                }
            fi
            if [[ "$ok" == "true" ]]; then
                if [[ -n "$agent_json_data" ]] && [[ -n "$tools_json_data" ]]; then
                    json_data="$(echo "[$agent_json_data,$tools_json_data]" | jq 'flatten')"
                elif [[ -n "$agent_json_data" ]]; then
                    json_data="$agent_json_data"
                elif [[ -n "$tools_json_data" ]]; then
                    json_data="$tools_json_data"
                fi
                if [[ -n "$json_data" ]]; then
                    echo "Build $declarations_file"
                    echo "$json_data" > "$declarations_file"
                fi
            fi
        fi
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
    json="$("$cmd" "scripts/build-declarations.$lang" "$tools_path" | jq 'map(. + {agent: true})')"
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
    test-demo@tool
}

# @cmd Test demo tools
# @alias tool:test-demo
test-demo@tool() {
    for item in "${LANG_CMDS[@]}"; do
        lang="${item%:*}"
        tool="demo_$lang.$lang"
        echo "---- Test $tool ---"
        argc build-bin@tool "$tool"
        argc run@tool $tool '{
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
    mkdir -p "$TMP_DIR"
    names_file="$TMP_DIR/agents.txt"
    argc list@agent > "$names_file"
    argc build@agent --names-file "$names_file"
    test-demo@agent
}

# @cmd Test demo agents
# @alias agent:test-demo
test-demo@agent() {
    echo "---- Test demo agent ---"
    args=(demo get_ipinfo '{}')
    argc run@agent "${args[@]}"
    for item in "${LANG_CMDS[@]}"; do
        cmd="${item#*:}"
        lang="${item%:*}"
        echo "---- Test agents/demo/tools.$lang ---"
        if [[ "$cmd" == "sh" ]]; then
            "$(argc --argc-shell-path)" ./scripts/run-agent.sh "${args[@]}"
        elif command -v "$cmd" &> /dev/null; then
            $cmd ./scripts/run-agent.$lang "${args[@]}"
            echo
        fi
    done
}

# @cmd Clean tools
# @alias tool:clean
clean@tool() {
    _choice_tool | sed -E 's/\.([a-z]+)$//' |  xargs -I{} rm -rf "$BIN_DIR/{}"
    rm -rf functions.json
}

# @cmd Clean agents
# @alias agent:clean
clean@agent() {
    _choice_agent | xargs -I{} rm -rf "$BIN_DIR/{}"
    _choice_agent | xargs -I{} rm -rf agents/{}/functions.json
}

# @cmd Link a tool as web_search tool
#
# Example:
#   argc link-web-search web_search_perplexity.sh
# @arg tool![`_choice_web_search`]  The tool work as web_search
link-web-search() {
    _link_tool $1 web_search
}

# @cmd Link a tool as code_interpreter tool
#
# Example:
#   argc link-code-interpreter execute_py_code.py
# @arg tool![`_choice_code_interpreter`]  The tool work as code_interpreter
link-code-interpreter() {
    _link_tool $1 code_interpreter
}

# @cmd Install this repo to aichat functions_dir
install() {
    functions_dir="$(aichat --info | grep -w functions_dir | awk '{$1=""; print substr($0,2)}')"
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

# @cmd Run mcp command
# @arg args~[?`_choice_mcp_args`] The mcp command and arguments
mcp() {
    bash ./scripts/mcp.sh "$@"
}

# @cmd Create a boilplate tool script
# @alias tool:create
# @arg args~
create@tool() {
    ./scripts/create-tool.sh "$@"
}

# @cmd Show pre-requisite tool versions
version() {
    uname -a
    if command -v aichat &> /dev/null; then
        aichat --version
    fi
    argc --argc-version
    jq --version
    ls --version 2>&1 | head -n 1
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
            break
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
        if [[ "$cmd" == "python" && -d "$VENV_DIR" ]]; then
            run="call \"$(_normalize_path "$PWD/$VENV_DIR/Scripts/activate.bat")\" && python"
        else
            run="\"$(_normalize_path "$(which $cmd)")\""
        fi
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

_build_py_shim() {
    kind="$1"
    lang="$2"
    cat <<-'EOF' | sed -e "s|__ROOT_DIR__|$PWD|g" -e "s|__VENV_DIR__|$VENV_DIR|g" -e "s/__KIND__/$kind/g"
#!/usr/bin/env bash
set -e

if [[ -f "__ROOT_DIR__/__VENV_DIR__/bin/activate" ]]; then
    source "__ROOT_DIR__/__VENV_DIR__/bin/activate"
fi

python "__ROOT_DIR__/scripts/run-__KIND__.py" "$(basename "$0")" "$@"
EOF
}

_link_tool() {
    from="$1"
    to="$2.${1##*.}"
    rm -rf tools/$to
    if _is_win; then
        (cd tools && cp -f $from $to)
    else
        (cd tools && ln -s $from $to)
    fi
    (cd tools && ls -l $to)
}

_ask_json_data() {
    declaration="$1"
    echo 'Missing the JSON data but here are its properties:'
    echo "$declaration" | ./scripts/declarations-util.sh pretty-print | sed -n '2,$s/^/>/p'
    echo 'Generate placeholder data:'
    data="$(echo "$declaration" | _declarations_json_data)"
    echo ">  $data"
    read -e -r -p 'JSON data (Press ENTER to use placeholder): ' res
    if [[ -z "$res" ]]; then
        argc_json="$data"
    else
        argc_json="$res"
    fi
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

_argc_before() {
    if [[ -d ".venv/bin/activate" ]]; then
        source .venv/bin/activate
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

_choice_web_search() {
    _choice_tool | grep '^web_search_'
}

_choice_code_interpreter() {
    _choice_tool | grep '^execute_.*_code'
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
    argc generate-declarations@agent "$1" --oneline | sed "$expr"
}

_choice_mcp_args() {
    if [[ "$ARGC_COMPGEN" -eq 1 ]]; then
        args=( "${argc__positionals[@]}" )
        args[-1]="$ARGC_LAST_ARG"
        argc --argc-compgen generic scripts/mcp.sh mcp "${args[@]}"
    else
        :;
    fi
}

_die() {
    echo "$*" >&2
    exit 1
}

if _is_win; then set -o igncr; fi

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
