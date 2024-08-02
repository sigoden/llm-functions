#!/usr/bin/env bash
set -e

# @describe Write the contents to a file at the specified path.
# If the file exists, only the necessary changes will be applied.
# If the file doesn't exist, it will be created.
# Always provide the full intended contents of the file.

# @option --path! The path of the file to write to
# @option --contents! The full contents to write to the file

main() {
    _guard_path "$argc_path" Write
    mkdir -p "$(dirname "$argc_path")"
    printf "%s" "$argc_contents" > "$argc_path"
    echo "The contents written to: $argc_path" >> "$LLM_OUTPUT"
}

_guard_path() {
    path="$(realpath "$1")"
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
