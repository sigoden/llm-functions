#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Read contents of file(s) at the specified path(s) with optional line selection.
# @option --path+! The path(s) of the file to read
# @option --start-line The starting line number (1-based)
# @option --end-line The ending line number (inclusive)
# @option --lines The specific lines to read (comma-separated, e.g., 1,3,5)

# @env LLM_OUTPUT=/dev/fd/1 The output path

main() {
    for path in "${argc_path[@]}"; do
        if [[ ! -f "$path" ]]; then
            echo "Error: File '$path' not found." >&2
            continue
        fi

        echo "--- File: $path ---" >&1

        if [[ -n "$argc_lines" ]]; then
            # Convert comma-separated lines to sed format
            sed_expr=$(echo "$argc_lines" | sed 's/,/p;/g')p
            sed -n "$sed_expr" "$path" >&1
        elif [[ -n "$argc_start_line" ]] || [[ -n "$argc_end_line" ]]; then
            local start="${argc_start_line:-1}"
            local end="${argc_end_line:-$(wc -l < "$path")}"
            sed -n "${start},${end}p" "$path" >&1
        else
            cat "$path" >&1
        fi
        echo -e "\n" >&1
    done
}

eval "$(argc --argc-eval "$0" "$@")"
