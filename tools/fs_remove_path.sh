#!/usr/bin/env bash
set -e

# @describe Remove the file or directory at the specified path.

# @env FS_BASE_DIR=. The base dir
# @option --path! The path of the file or directory to remove

main() {
    path="$FS_BASE_DIR/$argc_path"
    rm -rf "$path"
    echo "Path removed: $path"
}

eval "$(argc --argc-eval "$0" "$@")"
