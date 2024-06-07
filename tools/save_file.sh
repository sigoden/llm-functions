#!/usr/bin/env bash
set -e

# @describe Saves the contents to a file called `file_name` and returns the file path if successful.
# @option --file-name! The name of the file to save to.
# @option --contents! The contents to save.

main() {
    base_dir="${LLM_TOOL_CACHE_DIR:-/tmp}"
    mkdir -p "$base_dir"
    output_file="$base_dir/$argc_file_name"
    echo "$argc_contents" > "$output_file"
    echo "$output_file"
}

eval "$(argc --argc-eval "$0" "$@")"
