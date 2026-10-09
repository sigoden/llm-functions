#!/data/data/com.termux/files/usr/bin/bash
# find_port.sh - Find process listening on a specific port
# Requires: netstat, ss, lsof, or termux-netstat (from termux-tools)

set -euo pipefail

VERSION="1.0.0"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

show_help() {
    cat <<EOF
Usage: find_port.sh [OPTIONS] PORT

Find process listening on a specific port.

Options:
  -p, --port PORT       Port number to search (required)
  -a, --all             Show all matches (not just LISTEN)
  -v, --verbose         Verbose output
  -j, --json            Output as JSON
  -k, --kill            Kill the process (requires confirmation)
  -f, --force           Force kill without confirmation (use with -k)
  -h, --help            Show this help
  --version             Show version

Examples:
  find_port.sh 8080              # Find process on port 8080
  find_port.sh -a 3000           # Show all connections on port 3000
  find_port.sh -j 22             # JSON output for port 22
  find_port.sh -k 8080           # Kill process on port 8080

Supported tools (auto-detected): ss, netstat, lsof, termux-netstat
EOF
}

log() {
    local level="$1"
    shift
    local msg="$*"
    local timestamp=$(date '+%H:%M:%S')
    case "$level" in
        ERROR) echo -e "${RED}[ERROR]${NC} $msg" >&2 ;;
        WARN)  echo -e "${YELLOW}[WARN]${NC} $msg" >&2 ;;
        INFO)  echo -e "${BLUE}[INFO]${NC} $msg" ;;
        DEBUG) [[ "$VERBOSE" == "true" ]] && echo -e "${CYAN}[DEBUG]${NC} $msg" ;;
    esac
}

detect_tool() {
    for tool in ss netstat lsof termux-netstat; do
        if command -v "$tool" >/dev/null 2>&1; then
            echo "$tool"
            return 0
        fi
    done
    return 1
}

find_with_ss() {
    local port="$1"
    local all="$2"
    local state_filter="LISTEN"
    [[ "$all" == "true" ]] && state_filter=""

    ss -tlnp "sport = :$port" 2>/dev/null | awk -v state="$state_filter" '
        NR==1 {next}
        state=="" || $1 ~ state {
            pid_cmd=$NF
            gsub(/.*pid=/, "", pid_cmd)
            gsub(/,.*/, "", pid_cmd)
            split(pid_cmd, a, "/")
            pid=a[1]
            cmd=a[2]
            printf "%s|%s|%s|%s|%s\n", $1, $5, pid, cmd, $NF
        }'
}

find_with_netstat() {
    local port="$1"
    local all="$2"
    local state_filter="LISTEN"
    [[ "$all" == "true" ]] && state_filter=""

    netstat -tlnp 2>/dev/null | awk -v port=":$port" -v state="$state_filter" '
        $4 ~ port && (state=="" || $6 ~ state) {
            pid_cmd=$7
            gsub(/.*\//, "", pid_cmd)
            split(pid_cmd, a, "/")
            pid=a[1]
            cmd=a[2]
            printf "%s|%s|%s|%s|%s\n", $1, $4, pid, cmd, $7
        }'
}

find_with_lsof() {
    local port="$1"
    local all="$2"

    lsof -i ":$port" 2>/dev/null | awk -v all="$all" '
        NR==1 {next}
        all=="true" || $8 == "LISTEN" {
            printf "%s|%s|%s|%s|%s\n", $8, $9, $2, $1, $0
        }'
}

find_with_termux_netstat() {
    local port="$1"
    local all="$2"
    local state_filter="LISTEN"
    [[ "$all" == "true" ]] && state_filter=""

    termux-netstat -t 2>/dev/null | awk -v port=":$port" -v state="$state_filter" '
        $4 ~ port && (state=="" || $6 ~ state) {
            pid_cmd=$7
            gsub(/.*\//, "", pid_cmd)
            split(pid_cmd, a, "/")
            pid=a[1]
            cmd=a[2]
            printf "%s|%s|%s|%s|%s\n", $1, $4, pid, cmd, $7
        }'
}

get_process_info() {
    local pid="$1"
    if [[ -z "$pid" || "$pid" == "-" ]]; then
        echo "N/A|N/A|N/A"
        return
    fi

    local user=$(ps -o user= -p "$pid" 2>/dev/null | xargs)
    local cmd=$(ps -o cmd= -p "$pid" 2>/dev/null | xargs)
    local cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || echo "N/A")
    echo "$user|$cmd|$cwd"
}

format_output() {
    local tool="$1"
    local port="$2"
    local all="$3"
    local json="$4"

    local results=()
    local header_printed=false

    case "$tool" in
        ss)       results=($(find_with_ss "$port" "$all")) ;;
        netstat)  results=($(find_with_netstat "$port" "$all")) ;;
        lsof)     results=($(find_with_lsof "$port" "$all")) ;;
        termux-netstat) results=($(find_with_termux_netstat "$port" "$all")) ;;
    esac

    if [[ ${#results[@]} -eq 0 ]]; then
        if [[ "$json" == "true" ]]; then
            echo '{"port": '$port', "processes": []}'
        else
            log "INFO" "No process found listening on port $port"
        fi
        return 1
    fi

    if [[ "$json" == "true" ]]; then
        local json_arr="["
        for i in "${!results[@]}"; do
            IFS='|' read -r proto local_addr pid cmd raw <<< "${results[$i]}"
            local proc_info=$(get_process_info "$pid")
            IFS='|' read -r user full_cmd cwd <<< "$proc_info"

            [[ $i -gt 0 ]] && json_arr+=","
            json_arr+=$(jq -n \
                --arg proto "$proto" \
                --arg local_addr "$local_addr" \
                --arg pid "$pid" \
                --arg cmd "$cmd" \
                --arg user "$user" \
                --arg full_cmd "$full_cmd" \
                --arg cwd "$cwd" \
                '{protocol: $proto, local_address: $local_addr, pid: ($pid|tonumber), command: $cmd, user: $user, full_command: $full_cmd, cwd: $cwd}')
        done
        json_arr+="]"
        jq -n --argjson port "$port" --argjson processes "$json_arr" '{port: $port, processes: $processes}'
    else
        echo -e "${GREEN}Port $port:${NC}"
        printf "%-8s %-22s %-8s %-20s %-10s %s\n" "PROTO" "LOCAL ADDRESS" "PID" "COMMAND" "USER" "CWD"
        printf "%-8s %-22s %-8s %-20s %-10s %s\n" "------" "-------------" "---" "-------" "----" "---"

        for result in "${results[@]}"; do
            IFS='|' read -r proto local_addr pid cmd raw <<< "$result"
            local proc_info=$(get_process_info "$pid")
            IFS='|' read -r user full_cmd cwd <<< "$proc_info"

            # Truncate long fields
            local short_cmd="${cmd:0:20}"
            local short_user="${user:0:10}"
            local short_cwd="${cwd:0:40}"

            printf "%-8s %-22s %-8s %-20s %-10s %s\n" "$proto" "$local_addr" "$pid" "$short_cmd" "$short_user" "$short_cwd"
        done
    fi
    return 0
}

kill_process() {
    local pid="$1"
    local force="$2"

    if [[ -z "$pid" || "$pid" == "-" || "$pid" == "N/A" ]]; then
        log "ERROR" "Invalid PID: $pid"
        return 1
    fi

    # Verify process exists
    if ! kill -0 "$pid" 2>/dev/null; then
        log "ERROR" "Process $pid not found"
        return 1
    fi

    local cmd=$(ps -o cmd= -p "$pid" 2>/dev/null | xargs)
    log "WARN" "About to kill PID $pid ($cmd)"

    if [[ "$force" != "true" ]]; then
        read -p "Confirm kill? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log "INFO" "Cancelled"
            return 0
        fi
    fi

    if [[ "$force" == "true" ]]; then
        kill -9 "$pid" 2>/dev/null && log "INFO" "Force killed PID $pid" || log "ERROR" "Failed to kill PID $pid"
    else
        kill -15 "$pid" 2>/dev/null && log "INFO" "Sent SIGTERM to PID $pid" || log "ERROR" "Failed to send SIGTERM to PID $pid"
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            log "WARN" "Process still alive, sending SIGKILL"
            kill -9 "$pid" 2>/dev/null && log "INFO" "Force killed PID $pid"
        fi
    fi
}

main() {
    local port=""
    local all="false"
    local verbose="false"
    local json="false"
    local kill="false"
    local force="false"

    VERBOSE="$verbose"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            -p|--port)
                port="$2"
                shift 2
                ;;
            -a|--all)
                all="true"
                shift
                ;;
            -v|--verbose)
                verbose="true"
                VERBOSE="true"
                shift
                ;;
            -j|--json)
                json="true"
                shift
                ;;
            -k|--kill)
                kill="true"
                shift
                ;;
            -f|--force)
                force="true"
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            --version)
                echo "find_port.sh v$VERSION"
                exit 0
                ;;
            *)
                if [[ -z "$port" && "$1" =~ ^[0-9]+$ ]]; then
                    port="$1"
                else:
                    log "ERROR" "Unknown option: $1"
                    show_help
                    exit 1
                fi
                shift
                ;;
        esac
    done

    # Validate port
    if [[ -z "$port" ]]; then
        log "ERROR" "Port number required"
        show_help
        exit 1
    fi

    if [[ ! "$port" =~ ^[0-9]+$ ]] || [[ "$port" -lt 1 || "$port" -gt 65535 ]]; then
        log "ERROR" "Invalid port: $port (must be 1-65535)"
        exit 1
    fi

    # Detect tool
    local tool=$(detect_tool)
    if [[ -z "$tool" ]]; then
        log "ERROR" "No supported network tool found (need ss, netstat, lsof, or termux-netstat)"
        log "INFO" "Install with: pkg install net-tools iproute2 lsof termux-tools"
        exit 1
    fi

    log "DEBUG" "Using tool: $tool"

    # Find processes
    local results
    results=$(format_output "$tool" "$port" "$all" "$json")
    local find_status=$?

    if [[ "$json" != "true" ]]; then
        echo "$results"
    fi

    # Kill if requested
    if [[ "$kill" == "true" && $find_status -eq 0 ]]; then
        # Extract PIDs from results
        local pids=()
        case "$tool" in
            ss)       mapfile -t pids < <(find_with_ss "$port" "$all" | cut -d'|' -f3) ;;
            netstat)  mapfile -t pids < <(find_with_netstat "$port" "$all" | cut -d'|' -f3) ;;
            lsof)     mapfile -t pids < <(find_with_lsof "$port" "$all" | cut -d'|' -f3) ;;
            termux-netstat) mapfile -t pids < <(find_with_termux_netstat "$port" "$all" | cut -d'|' -f3) ;;
        esac

        for pid in "${pids[@]}"; do
            [[ "$pid" != "-" && "$pid" != "N/A" ]] && kill_process "$pid" "$force"
        done
    fi

    exit $find_status
}

main "$@"
