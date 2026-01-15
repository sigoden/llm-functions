#!/usr/bin/env bash
set -e

# @describe Show changes between commits, commit and working tree, etc.
# @flag --staged Show changes that are staged for commit.
# @option --target The specific file or branch to diff (optional).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local args=()
    if [[ -n "$argc_staged" ]]; then
        args+=("--staged")
    fi
    
    if [[ -n "$argc_target" ]]; then
        args+=("$argc_target")
    fi

    # Using --color=never ensures the LLM sees clean text, not ANSI escape codes
    git diff --no-color "${args[@]}" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
