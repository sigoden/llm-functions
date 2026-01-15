#!/usr/bin/env bash
set -e

# @describe List currently running processes with optional filtering.
# @option --name Filter processes by command name.
# @flag --full Display full command line (wider output).
# @option --limit <INT> Limit the number of results (default: 10).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local limit="${argc_limit:-10}"
    
    # Headers: User, PID, CPU%, MEM%, Command
    local format="user,pid,%cpu,%mem,comm"
    if [[ -n "$argc_full" ]]; then
        format="user,pid,%cpu,%mem,args"
    fi

    if [[ -n "$argc_name" ]]; then
        # Use pgrep to find PIDs then ps to display details
        # We cap it at $limit to avoid token overflow
        pgrep -f "$argc_name" | head -n "$limit" | xargs -r ps -o "$format" --sort=-%cpu >> "$LLM_OUTPUT" || echo "No process found matching '$argc_name'" >> "$LLM_OUTPUT"
    else
        # Top processes by CPU usage
        ps -e -o "$format" --sort=-%cpu | head -n "$((limit + 1))" >> "$LLM_OUTPUT"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
