#!/usr/bin/env bash
set -e

main() {
    root_dir="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"
    self_name=run-tool.sh
    parse_argv "$@"
    setup_env
    tool_path="$root_dir/tools/$tool_name.sh"
    run 
}

parse_argv() {
    if [[ "$0" == *"$self_name" ]]; then
        tool_name="$1"
        tool_data="$2"
    else
        tool_name="$(basename "$0")"
        tool_data="$1"
    fi
    if [[ "$tool_name" == *.sh ]]; then
        tool_name="${tool_name:0:$((${#tool_name}-3))}"
    fi
    if [[ -z "$tool_data" ]] || [[ -z "$tool_name" ]]; then
        die "usage: ./run-tool.sh <tool-name> <tool-data>"
    fi
}

setup_env() {
    load_env "$root_dir/.env" 
    export LLM_ROOT_DIR="$root_dir"
    export LLM_TOOL_NAME="$tool_name"
    export LLM_TOOL_CACHE_DIR="$LLM_ROOT_DIR/cache/$tool_name"
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
    if [[ -z "$tool_data" ]]; then
        die "error: no JSON data"
    fi

    if [[ "$OS" == "Windows_NT" ]]; then
        set -o igncr
        tool_path="$(cygpath -w "$tool_path")"
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
    args="$(echo "$tool_data" | jq -r "$jq_script" 2>/dev/null)" || {
        die "error: invalid JSON data"
    }

    no_llm_output=0
    if [[ -z "$LLM_OUTPUT" ]]; then
        no_llm_output=1
        export LLM_OUTPUT="$(mktemp)"
    fi
    eval "'$tool_path' $args"
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
    local env_name env_value show_result=0
    env_name="LLM_TOOL_DUMP_RESULT_$(echo "$LLM_TOOL_NAME" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
    env_value="${!env_name}"
    if [[ "$LLM_TOOL_DUMP_RESULT" == "1" || "$LLM_TOOL_DUMP_RESULT" == "true" ]]; then
        if [[ "$env_value" != "0" && "$env_value" != "false" ]]; then
            show_result=1
        fi
    else
        if [[ "$env_value" == "1" || "$env_value" == "true" ]]; then
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
