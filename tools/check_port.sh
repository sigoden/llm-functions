[#!/usr/bin/env bash
set -e

# @describe Check if a specific TCP/UDP port is currently in use or listening.
# @option --port! <INT> The port number to check.
# @option --proto[tcp|udp] The protocol to check (default: tcp).
# @flag --verbose Show the process name/PID using the port (requires sudo/permissions often).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local proto="${argc_proto:-tcp}"
    local port="$argc_port"
    
    # Use ss (Socket Statistics) as it is modern and standard on most Linux distros
    # -t (tcp) -u (udp) -l (listening) -n (numeric) -p (processes)
    
    local flags="-l -n"
    if [[ "$proto" == "tcp" ]]; then
        flags="$flags -t"
    else
        flags="$flags -u"
    fi

    if [[ -n "$argc_verbose" ]]; then
        flags="$flags -p"
    fi

    echo "Checking $proto port $port..." >> "$LLM_OUTPUT"
    
    # Grep specifically for the port pattern (e.g., :8080) to avoid partial matches
    if command -v ss &> /dev/null; then
        if ss $flags | grep -E ":$port\b"; then
            echo "Result: Port $port is IN USE." >> "$LLM_OUTPUT"
        else
            echo "Result: Port $port is FREE." >> "$LLM_OUTPUT"
        fi
    elif command -v lsof &> /dev/null; then
        if lsof -i :$port; then
            echo "Result: Port $port is IN USE." >> "$LLM_OUTPUT"
        else
            echo "Result: Port $port is FREE." >> "$LLM_OUTPUT"
        fi
    else
        echo "Error: Neither 'ss' nor 'lsof' commands found." >> "$LLM_OUTPUT"
        exit 1
    fi
}

eval "$(argc --argc-eval "$0" "$@")"]
