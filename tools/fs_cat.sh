#!/usr/bin/env bash
set -e

# @describe Read the contents of a file at the specified path.
# Use this when you need to examine the contents of an existing file.

# @env FS_BASE_DIR=. The base dir
# @option --path! The path of the file to read

main() {
    path="$FS_BASE_DIR/$argc_path"
    cat "$path"
}

eval "$(argc --argc-eval "$0" "$@")"
