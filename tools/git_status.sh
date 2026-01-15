#!/usr/bin/env bash
set -e

# @describe Check the status of the current git repository to see modified, staged, or untracked files.
# @flag --short Give the output in the short-format.

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
        echo "Error: Not a git repository." >> "$LLM_OUTPUT"
        exit 1
    fi

    if [[ -n "$argc_short" ]]; then
        git status --short >> "$LLM_OUTPUT"
    else
        git status >> "$LLM_OUTPUT"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
