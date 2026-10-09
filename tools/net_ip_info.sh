#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Get network IP information including public IP and network interfaces
# @flag --public-ip    Include public IP address
# @flag --interfaces   Include network interface information

main() {
    if ! command -v jq &> /dev/null; then
        echo '{"error": "jq is not installed"}' >&2
        return 1
    fi
    
    local output="{}"
    
    # If no flags are provided, default to both
    if [[ -z "$argc_public_ip" && -z "$argc_interfaces" ]]; then
        argc_public_ip=1
        argc_interfaces=1
    fi

    if [[ "$argc_public_ip" == "1" ]]; then
        local public_ip
        public_ip=$(curl -s --max-time 10 https://api.ipify.org 2>/dev/null) || public_ip="N/A"
        output=$(echo "$output" | jq -c ".public_ip = \"$public_ip\"")
    fi
    
    if [[ "$argc_interfaces" == "1" ]]; then
        local interfaces_json
        interfaces_json=$(ip -j addr show 2>/dev/null | jq '[.[] | select(.operstate == "UP") | {name: .ifname, addresses: [.addr_info[] | select(.family == "inet") | {address: .local, prefix: .prefixlen}]}]' 2>/dev/null || echo "[]")
        [[ -z "$interfaces_json" ]] && interfaces_json="[]"
        output=$(echo "$output" | jq -c ".interfaces = $interfaces_json")
    fi
    
    if [[ -n "${LLM_OUTPUT:-}" && "$LLM_OUTPUT" != "/dev/stdout" ]]; then
        echo "$output" >> "$LLM_OUTPUT"
    else
        echo "$output"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
