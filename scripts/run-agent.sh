#!/usr/bin/env bash
set -e

main() {
    root_dir="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"
    self_name=run-agent.sh
    parse_argv "$@"
    setup_env
    tools_path="$root_dir/agents/$agent_name/tools.sh"
    run 
}

parse_argv() {
    if [[ "$0" == *"$self_name" ]]; then
        agent_name="$1"
        agent_func="$2"
        agent_data="$3"
    else
        agent_name="$(basename "$0")"
        agent_func="$1"
        agent_data="$2"
    fi
    if [[ "$agent_name" == *.sh ]]; then
        agent_name="${agent_name:0:$((${#agent_name}-3))}"
    fi
    if [[ -z "$agent_data" ]] || [[ -z "$agent_func" ]] || [[ -z "$agent_name" ]]; then
        die "usage: ./run-agent.sh <agent-name> <agent-func> <agent-data>"
    fi
}

setup_env() {
    load_env "$root_dir/.env" 
    export LLM_ROOT_DIR="$root_dir"
    export LLM_AGENT_NAME="$agent_name"
    export LLM_AGENT_FUNC="$agent_func"
    export LLM_AGENT_ROOT_DIR="$LLM_ROOT_DIR/agents/$agent_name"
    export LLM_AGENT_CACHE_DIR="$LLM_ROOT_DIR/cache/$agent_name"
}

load_env() {
    local env_file="$1" env_vars
    if [[ -f "$env_file" ]]; then
        while IFS='=' read -r key value; do
            if [[ "$key" == $'#'* ]] || [[ -z "$key" ]]; then
                continue
            fi
            if [[ -z "${!key+x}" ]]; then
                env_vars="$env_vars $key=$value"
            fi
        done < <(cat "$env_file"; echo "")
        if [[ -n "$env_vars" ]]; then
            eval "export $env_vars"
        fi
    fi
}

run() {
    if [[ -z "$agent_data" ]]; then
        die "error: no JSON data"
    fi

    if [[ "$OS" == "Windows_NT" ]]; then
        set -o igncr
        tools_path="$(cygpath -w "$tools_path")"
        tool_data="$(echo "$tool_data" | sed 's/\\/\\\\/g')"
    fi

    jq_script="$(cat <<-'EOF'
def escape_shell_word:
  tostring
  | gsub("'"; "'\"'\"'")
  | gsub("\n"; "'$'\\n''")
  | "'\(.)'";
def to_args:
    to_entries | .[] | 
    (.key | split("_") | join("-")) as $key |
    if .value | type == "array" then
        .value | .[] | "--\($key) \(. | escape_shell_word)"
    elif .value | type == "boolean" then
        if .value then "--\($key)" else "" end
    else
        "--\($key) \(.value | escape_shell_word)"
    end;
[ to_args ] | join(" ")
EOF
)"
    args="$(echo "$agent_data" | jq -r "$jq_script" 2>/dev/null)" || {
        die "error: invalid JSON data"
    }

    no_llm_output=0
    if [[ -z "$LLM_OUTPUT" ]]; then
        no_llm_output=1
        export LLM_OUTPUT="$(mktemp)"
    fi
    eval "'$tools_path' '$agent_func' $args"
    if [[ "$no_llm_output" -eq 1 ]]; then
        cat "$LLM_OUTPUT"
    else
        dump_result
    fi
}

dump_result() {
    if [ ! -t 1 ]; then
        return;
    fi
    
    local agent_env_name agent_env_value func_env_name func_env_value show_result=0
    agent_env_name="LLM_AGENT_DUMP_RESULT_$(echo "$LLM_AGENT_NAME" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
    agent_env_value="${!agent_env_name:-"$LLM_AGENT_DUMP_RESULT"}"
    func_env_name="${agent_env_name}_$(echo "$LLM_AGENT_FUNC" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
    func_env_value="${!func_env_name}"
    if [[ "$agent_env_value" == "1" || "$agent_env_value" == "true" ]]; then
        if [[ "$func_env_value" != "0" && "$func_env_value" != "false" ]]; then
            show_result=1
        fi
    else
        if [[ "$func_env_value" == "1" || "$func_env_value" == "true" ]]; then
            show_result=1
        fi
    fi
    if [[ "$show_result" -ne 1 ]]; then
        return
    fi
    cat <<EOF
$(echo -e "\e[2m")----------------------
$(cat "$LLM_OUTPUT")
----------------------$(echo -e "\e[0m")
EOF
}

die() {
    echo "$*" >&2
    exit 1
}

main "$@"

