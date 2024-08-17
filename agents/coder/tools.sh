#!/usr/bin/env bash
set -e

# @cmd Create a new file at the specified path with contents.
# @option --path! The path where the file should be created
# @option --contents! The contents of the file
fs_create() {
    _guard_path "$argc_path" Create
    mkdir -p "$(dirname "$argc_path")"
    printf "%s" "$argc_contents" > "$argc_path"
    echo "File created: $argc_path" >> "$LLM_OUTPUT"
}

# @cmd Apply changes to a file. Use this when you need to edit an existing file.
# YOU ALWAYS PROVIDE THE FULL FILE CONTENTS WHEN EDITING. NO PARTIAL CONTENTS OR COMMENTS.
# YOU MUST PROVIDE THE FULL FILE CONTENTS.

# @option --path! The path of the file to edit
# @option --contents! The new contents to apply to the file
# @meta require-tools git
fs_edit() {
    if [[ -f "$argc_path" ]]; then
        _guard_path "$argc_path" Edit
        changed=0
        printf "%s" "$argc_contents" | git diff --no-index "$argc_path" - || {
            changed=1
        }
        if [[ "$changed" -eq 0 ]]; then
            echo "No changes detected." >> "$LLM_OUTPUT"
        else
            if [ -t 1 ]; then
                echo
                read -r -p "Apply changes? [Y/n] " ans
                if [[ "$ans" == "N" || "$ans" == "n" ]]; then
                    echo "Aborted!"
                    exit 1
                fi
            fi
            printf "%s" "$argc_contents" > "$argc_path"
            echo "Applied changes" >> "$LLM_OUTPUT"
        fi
    else
        echo "Not found file: $argc_path" >> "$LLM_OUTPUT"
    fi
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

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
