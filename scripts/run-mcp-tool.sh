#!/usr/bin/env bash

# Usage: ./run-mcp-tool.sh <tool-name> <tool-data>

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
    if [[ -z "$tool_data" ]]; then
        die "error: no JSON data"
    fi

    if [[ "$OS" == "Windows_NT" ]]; then
        set -o igncr
        tool_data="$(echo "$tool_data" | sed 's/\\/\\\\/g')"
    fi

    if [[ -z "$LLM_OUTPUT" ]]; then
        is_temp_llm_output=1
        export LLM_OUTPUT="$(mktemp)"
    fi

    if [[ -n "$LLM_MCP_SKIP_CONFIRM" ]]; then
        if grep -q -w -E "$LLM_MCP_SKIP_CONFIRM" <<<"$tool_name"; then
            skip_confirm=1
        fi
    fi
    if [[ -n "$LLM_MCP_NEED_CONFIRM" ]]; then
        if grep -q -w -E "$LLM_MCP_NEED_CONFIRM" <<<"$tool_name"; then
            skip_confirm=0
        fi
    fi
    if [[ -t 1 ]] && [[ "$skip_confirm" -ne 1 ]]; then
        read -r -p "Are you sure you want to continue? [Y/n] " ans
        if [[ "$ans" == "N" || "$ans" == "n" ]]; then
            echo "error: canceled!" 2>&1
            exit 1
        fi
    fi

    curl -sS "http://localhost:${MCP_BRIDGE_PORT:-8808}/tools/$tool_name" \
        -X POST \
        -H 'content-type: application/json' \
        -d "$tool_data" > "$LLM_OUTPUT"

    if [[ "$is_temp_llm_output" -eq 1 ]]; then
        cat "$LLM_OUTPUT"
    else
        dump_result "$tool_name" 
    fi
}

dump_result() {
    if [[ "$LLM_OUTPUT" == "/dev/stdout" ]] || [[ -z "$LLM_DUMP_RESULTS" ]] ||  [[ ! -t 1 ]]; then
        return;
    fi
    if grep -q -w -E "$LLM_DUMP_RESULTS" <<<"$1"; then
            cat <<EOF
$(echo -e "\e[2m")----------------------
$(cat "$LLM_OUTPUT")
----------------------$(echo -e "\e[0m")
EOF
    fi
}

main "$@"
