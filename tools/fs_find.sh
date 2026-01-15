#!/usr/bin/env bash
set -e

# @describe Search for files or directories matching a pattern. Essential for locating code or configs.
# @option --path! The root path to start searching from (e.g., "." or "/var/log").
# @option --name! The file pattern to match (e.g., "*.py", "config.yaml").
# @option --max-depth <INT> Limit the directory traversal depth (default: 5 to prevent infinite loops).
# @option --type[f|d] Filter by file (f) or directory (d).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local depth="${argc_max_depth:-5}"
    local type_flag=""
    
    if [[ "$argc_type" == "f" ]]; then
        type_flag="-type f"
    elif [[ "$argc_type" == "d" ]]; then
        type_flag="-type d"
    fi

    # Using find with safeguards
    find "$argc_path" -maxdepth "$depth" $type_flag -name "$argc_name" -not -path '*/.*' >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
