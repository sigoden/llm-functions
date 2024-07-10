#!/usr/bin/env bash
set -e

# @describe Write the contents to a file at the specified path.
# If the file exists, only the necessary changes will be applied.
# If the file doesn't exist, it will be created.
# Always provide the full intended contents of the file.

# @env FS_BASE_DIR=. The base dir
# @option --path! The path of the file to write to
# @option --contents! The full contents to write to the file

main() {
    path="$FS_BASE_DIR/$argc_path"
    mkdir -p "$(dirname "$path")"
    printf "%s" "$argc_contents" > "$path"
    echo "The contents written to: $path" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
