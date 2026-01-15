#!/usr/bin/env bash
set -e

# @describe Get a snapshot of system health including CPU load, memory usage, and disk space.
# @flag --verbose Include detailed process list (top 5 by CPU).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    {
        echo "=== System Uptime & Load ==="
        uptime
        echo ""
        
        echo "=== Memory Usage (MB) ==="
        free -m
        echo ""

        echo "=== Disk Usage ==="
        df -h | grep -v "tmpfs" | grep -v "udev"
        echo ""

        if [[ -n "$argc_verbose" ]]; then
            echo "=== Top 5 CPU Processes ==="
            ps -eo pid,ppid,cmd,%mem,%cpu --sort=-%cpu | head -n 6
        fi
    } >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
