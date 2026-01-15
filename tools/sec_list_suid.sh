#!/usr/bin/env bash
set -e

# @describe Find files with SUID active. These run with root file permissions and are common privilege escalation vectors.
# @option --path The root path to search (default: /usr/bin). Limit this to verify questionable directories.

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local s_path="${argc_path:-/usr/bin}"

    if [[ "$s_path" == "/" ]]; then
        echo "Error: Scanning root '/' is too slow. Be specific." >> "$LLM_OUTPUT"
        exit 1
    fi

    echo "Searching for SUID binaries in $s_path..." >> "$LLM_OUTPUT"
    # Find files with perm -4000 (SUID)
    find "$s_path" -perm /4000 -type f -exec ls -ld {} \; >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
