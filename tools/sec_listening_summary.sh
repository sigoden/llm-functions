#!/usr/bin/env bash
set -e

# @describe List network services listening for incoming connections. Distinguishes between local and public exposure.
# @flag --public-only Show only services listening on 0.0.0.0 or [::] (Exposed to LAN/WAN).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local cmd="ss -tulpn"

    echo "SERVICE              PROT LISTEN PORT   ADDRESS" >> "$LLM_OUTPUT"
    
    if [[ -n "$argc_public_only" ]]; then
        # Look for the wildcards 0.0.0.0 or [::] meaning "Any Interface"
        ss -tulpn | grep -E "0\.0\.0\.0|\[::\]" | awk '{print $7 " " $1 " " $2 " " $5}' | sed 's/users:(("//g' | sed 's/"))//g' >> "$LLM_OUTPUT"
    else
        ss -tulpn | tail -n+2 | awk '{print $7 " " $1 " " $2 " " $5}' | sed 's/users:(("//g' | sed 's/"))//g' >> "$LLM_OUTPUT"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
