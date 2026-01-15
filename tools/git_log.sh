#!/usr/bin/env bash
set -e

# @describe Show the commit logs.
# @option --max-count <INT> Limit the number of commits to show (default: 5).
# @flag --oneline Compress each commit to a single line.
# @flag --stat Include a diffstat (summary of changed files) for each commit.

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local count="${argc_max_count:-5}"
    local format_args=("-n" "$count")

    if [[ -n "$argc_oneline" ]]; then
        format_args+=("--oneline")
    fi
    
    if [[ -n "$argc_stat" ]]; then
        format_args+=("--stat")
    fi

    git log "${format_args[@]}" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
