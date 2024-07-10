#!/usr/bin/env bash
set -e

# @describe Create a new directory at the specified path.

# @env FS_BASE_DIR=. The base dir
# @option --path! The path of the directory to create

main() {
    path="$FS_BASE_DIR/$argc_path"
    mkdir -p "$path"
    echo "Directory created: $path" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
