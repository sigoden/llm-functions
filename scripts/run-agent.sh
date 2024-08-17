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
}

setup_env() {
    load_env "$root_dir/.env" 
    export LLM_ROOT_DIR="$root_dir"
    export LLM_AGENT_NAME="$agent_name"
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
        die "No JSON data"
    fi

    if [[ "$OS" == "Windows_NT" ]]; then
        set -o igncr
        tools_path="$(cygpath -w "$tools_path")"
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
    args="$(echo "$agent_data" | jq -r "$jq_script")" || {
        die "Invalid JSON data"
    }

    no_llm_output=0
    if [[ -z "$LLM_OUTPUT" ]]; then
        no_llm_output=1
        export LLM_OUTPUT="$(mktemp)"
    fi
    eval "'$tools_path' '$agent_func' $args"
    if [[ "$no_llm_output" -eq 1 ]]; then
        cat "$LLM_OUTPUT"
    fi
}

die() {
    echo "$*" >&2
    exit 1
}

main "$@"

