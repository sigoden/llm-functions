#!/usr/bin/env bash
set -e

# @describe Write the full file contents to a file at the specified path.

# @option --path! The path of the file to write to
# @option --contents! The full contents to write to the file

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    _guard_path "$argc_path" Write
    mkdir -p "$(dirname "$argc_path")"
    printf "%s" "$argc_contents" > "$argc_path"
    echo "The contents written to: $argc_path" >> "$LLM_OUTPUT"
}

_guard_path() {
    path="$(realpath -m "$1")"
    action="$2"
    if [[ ! "$path" == "$(pwd)"* ]]; then
        if [ -t 1 ]; then
            read -r -p "$action $path? [Y/n] " ans
            if [[ "$ans" == "N" || "$ans" == "n" ]]; then
                echo "Aborted!"
                exit 1
            fi
        fi
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
