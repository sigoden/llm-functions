#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe List all files and directories at the specified path.

# @option --path! The path of the directory to list

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    ls_args=()
    if [[ "$LLM_OUTPUT_COLOR" == "1" ]]; then
        # Use --color=always to ensure color is output even when not a TTY (e.g., redirected to file)
        ls_args+=("--color=always")
    fi
    # Changed from 'ls -1' to 'ls' to allow for potential color output from ls
    ls "${ls_args[@]}" "$argc_path"
}

eval "$(argc --argc-eval "$0" "$@")"
set -eo pipefail
