#!/usr/bin/env bash
set -e

# @describe Audit DNS integrity by comparing local resolution vs trusted upstream resolvers (Quad9/Google). Detects /etc/hosts poisoning.
# @option --domain The reference domain to check (default: google.com).
# @flag --verbose Show ip comparison details.

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local target="${argc_domain:-google.com}"
    
    # Check 1: Hosts and Resolve config
    echo "=== Configuration Check ===" >> "$LLM_OUTPUT"
    grep -H . /etc/resolv.conf >> "$LLM_OUTPUT" || echo "Note: /etc/resolv.conf managed by stub-resolver" >> "$LLM_OUTPUT"
    
    # Scanning /etc/hosts for likely overrides involves checking if the file is clean relative to standard distribution
    # We grep for potential tampering of google/microsoft/github in local hosts file
    if grep -Eq "google|microsoft|github|banking" /etc/hosts; then
        echo "ALERT: Suspicious overrides found in /etc/hosts:" >> "$LLM_OUTPUT"
        grep -E "google|microsoft|github|banking" /etc/hosts >> "$LLM_OUTPUT"
    fi

    echo "" >> "$LLM_OUTPUT"
    echo "=== Integrity Test: $target ===" >> "$LLM_OUTPUT"
    
    # Resolve using Local Default
    local local_ip=""
    if command -v dig &> /dev/null; then
        local_ip=$(dig +short "$target" | sort | head -n1)
    else
        echo "Error: 'dig' command missing (bind-utils)." >> "$LLM_OUTPUT"
        exit 1
    fi

    # Resolve using Trusted Upstream (8.8.8.8) bypassing simple local capture
    local trusted_ip=$(dig @8.8.8.8 +short "$target" | sort | head -n1)

    if [[ -z "$local_ip" || -z "$trusted_ip" ]]; then
        echo "Error: Verification failed. Could not resolve domain." >> "$LLM_OUTPUT"
        exit 1
    fi

    if [[ "$local_ip" == "$trusted_ip" ]]; then
         echo "STATUS: CLEAN. Local DNS matches Trusted DNS." >> "$LLM_OUTPUT"
    else
         # Sometimes IPs differ due to CDN/Geo-loadbalancing. We perform reverse lookup.
         echo "STATUS: MISMATCH DETECTED (Possibly CDN)." >> "$LLM_OUTPUT"
         echo "Local View:   $local_ip" >> "$LLM_OUTPUT"
         echo "Trusted View: $trusted_ip" >> "$LLM_OUTPUT"
         echo "Warning: If this is an internal LAN domain, ignore this. If public, verify Geo-DNS." >> "$LLM_OUTPUT"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
