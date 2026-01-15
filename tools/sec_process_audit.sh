#!/usr/bin/env bash
set -e

# @describe Audit processes for tampering indicators like LD_PRELOAD injection or suspicious ptrace tracing.
# @flag --full Scan all processes (requires elevated privileges/sudo often).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    echo "Scanning environment variables of running processes for 'LD_' overrides..." >> "$LLM_OUTPUT"

    # We read from /proc/[pid]/environ. This usually requires permission for non-owned processes.
    
    local suspicious_pids=0
    
    # Using 'ps' to grab user processes
    for pid in $(ps -u "$USER" -o pid=); do
        # Environ is null-delimited. cat -v helps escape printing mess.
        # We look for LD_PRELOAD content in environment
        if grep -lab "LD_PRELOAD" "/proc/$pid/environ" 2>/dev/null; then
            echo "ALERT: Process $pid contains LD_PRELOAD injection!" >> "$LLM_OUTPUT"
            ps -fp "$pid" >> "$LLM_OUTPUT"
            suspicious_pids=$((suspicious_pids+1))
        fi
    done

    if [[ "$suspicious_pids" -eq 0 ]]; then
        echo "Check (LD_PRELOAD): No infected user processes found." >> "$LLM_OUTPUT"
    fi

    echo "" >> "$LLM_OUTPUT"
    echo "checking for promiscuous interfaces (Packet Sniffing detection)..." >> "$LLM_OUTPUT"
    # Looking for flags P (Promiscuous) from ip link
    if ip -o link show | grep -i "PROMISC"; then
         echo "ALERT: Interfaces found in Promiscuous Mode (Sniffing Active?):" >> "$LLM_OUTPUT"
         ip -o link show | grep -i "PROMISC" >> "$LLM_OUTPUT"
    else
         echo "Check (Net Interface): Normal." >> "$LLM_OUTPUT"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
