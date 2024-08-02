#!/usr/bin/env bash
set -e

# @cmd Create a new file at the specified path with content.
# @option --path! The path where the file should be created
# @option --content! The content of the file
fs_create() {
    _guard_path "$argc_path" Create
    printf "%s" "$argc_content" > "$argc_path"
    echo "File created: $argc_path" >> "$LLM_OUTPUT"
}

# @cmd Apply changes to a file. Use this when you need to edit an existing file.
# YOU ALWAYS PROVIDE THE FULL FILE CONTENT WHEN EDITING. NO PARTIAL CONTENT OR COMMENTS.
# YOU MUST PROVIDE THE FULL FILE CONTENT.

# @option --path! The path of the file to edit
# @option --content! The new content to apply to the file
# @meta require-tools git
fs_edit() {
    if [[ -f "$argc_path" ]]; then
        _guard_path "$argc_path" Edit
        changed=0
        printf "%s" "$argc_content" | git diff --no-index "$argc_path" - || {
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
            printf "%s" "$argc_content" > "$argc_path"
            echo "Applied changes" >> "$LLM_OUTPUT"
        fi
    else
        echo "Not found file: $argc_path" >> "$LLM_OUTPUT"
    fi
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

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
