#!/usr/bin/env bash
set -e

# @describe Verify integrity of system config files. Scans for files that have changed from the installed package defaults.
# @flag --all Verify all installed packages (Slow! Use with caution).
# @option --package Limit verification to a specific package (e.g. openssh-server, sudo).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local target="-a"

    if [[ -n "$argc_package" ]]; then
        target="$argc_package"
    fi

    # Bazzite/Fedora Atomic users can mostly rely on OSTree for /usr
    # We focus heavily on configuration drift in /etc
    
    echo "Scanning validation logic..." >> "$LLM_OUTPUT"
    
    # 5=checksum, S=size, L=Symlink, T=Time, D=Device, U=User, G=Group, M=Mode
    # We filter specifically for '5' which means checksum (content) differ
    rpm -V $target | grep '^..5' >> "$LLM_OUTPUT" || echo "Integrity Check Passed: No modified checksums found in target scope." >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
