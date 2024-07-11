#!/usr/bin/env bash
set -e

# @env FS_BASE_DIR=. The base dir

# @cmd Create a new file at the specified path with content.
# @option --path! The path where the file should be created
# @option --content! The content of the file
fs_create() {
    path="$FS_BASE_DIR/$argc_path"
    printf "%s" "$argc_content" > "$path"
    echo "File created: $path" >> "$LLM_OUTPUT"
}

# @cmd Edit the file.
# @option --path! The path of the file to edit
# @option --content! The new content to apply to the file
# @meta require-tools git
fs_edit() {
    path="$FS_BASE_DIR/$argc_path"
    if [[ -f "$path" ]]; then
        changed=0
        printf "%s" "$argc_content" | git diff --no-index "$path" - || {
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
            printf "%s" "$argc_content" > "$path"
            echo "Applied changes" >> "$LLM_OUTPUT"
        fi
    else
        echo "Not found file: $path" >> "$LLM_OUTPUT"
    fi
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
