#!/usr/bin/env bash
set -e

# @describe Create a new directory at the specified path.

# @option --path! The path of the directory to create

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    mkdir -p "$argc_path"
    echo "Directory created: $argc_path" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
