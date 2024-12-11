#!/usr/bin/env bash
set -e

main() {
    root_dir="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"
    self_name=run-mcp-tool.sh
    parse_argv "$@"
    load_env "$root_dir/.env" 
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
    no_llm_output=0
    if [[ -z "$LLM_OUTPUT" ]]; then
        no_llm_output=1
        export LLM_OUTPUT="$(mktemp)"
    fi
    curl -sS "http://localhost:${MCP_BRIDGE_PORT:-8808}/tools/$tool_name" \
        -X POST \
        -H 'content-type: application/json' \
        -d "$tool_data" > "$LLM_OUTPUT"

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

main "$@"
