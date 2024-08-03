#!/usr/bin/env bash
set -e

# @describe Remove the file or directory at the specified path.

# @option --path! The path of the file or directory to remove

main() {
    if [[ -f "$argc_path" ]]; then
        _guard_path "$argc_path" Remove
        rm -rf "$argc_path"
    fi
    echo "Path removed: $argc_path" >> "$LLM_OUTPUT"
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
