#!/usr/bin/env bash
set -e

# @describe Check dependencies
#
# Examples:
#   ./scripts/check-deps.sh tools/execute_sql_code.sh
#   ./scripts/check-deps.sh agents/json-viewer/tools.js
#
# @arg script-path! The script file path

main() {
    script_path="$argc_script_path"
    if [[ ! -f "$script_path" ]]; then
        _exit "✗ not found $script_path"
    fi
    ext="${script_path##*.}"
    if [[ "$script_path" == tools/* ]]; then
        if [[ "$ext" == "sh" ]]; then
            check_sh_dependencies
        fi
    elif [[ "$script_path" == agents/* ]]; then
        if [[ "$ext" == "sh" ]]; then
            check_sh_dependencies
        elif [[ "$ext" == "js" ]]; then
            check_agent_js_dependencies
        elif [[ "$ext" == "py" ]]; then
            check_agent_py_dependencies
        fi
    fi
}

check_sh_dependencies() {
    deps=( $(sed -E -n 's/.*@meta require-tools //p' "$script_path") )
    missing_deps=()
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &> /dev/null; then
            missing_deps+=("$dep")
        fi
    done
    if [[ -n "${missing_deps}" ]]; then
        _exit "✗ missing tools: ${missing_deps[*]}"
    fi
}

check_agent_js_dependencies() {
    agent_dir="$(dirname "$script_path")"
    if [[ -f "$agent_dir/package.json" ]]; then
        npm ls --prefix="$agent_dir" --depth=0 --silent >/dev/null 2>&1 || \
            _exit "✗ missing node modules, FIX: cd $agent_dir && npm install"
    fi
}

check_agent_py_dependencies() {
    agent_dir="$(dirname "$script_path")"
    if [[ -f "$agent_dir/requirements.txt" ]]; then
        python <(cat "$agent_dir/requirements.txt" | sed -E -n 's/^([A-Za-z_]+).*/import \1/p') >/dev/null 2>&1 || \
            _exit "✗ missing python modules, FIX: cd $agent_dir && pip install -r requirements.txt"
    fi
}

_exit() {
    echo "$*" >&2
    exit 0
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
