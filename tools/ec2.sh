#!/data/data/com.termux/files/usr/bin/env bash
set -uo pipefail
# Note: We removed -e to allow graceful handling of expected failures (e.g., command not found)

# @describe Execute arbitrary shell command and return full output.
# @option --command! <STRING> Command to run
# @option --timeout <DURATION> Duration for the command (e.g. 60s, 1m, 2h)
# @option --connect-timeout <DURATION> Connection timeout for curl commands (e.g. 10s, default: 10s)
# @option --max-time <DURATION> Max transfer time for curl commands (e.g. 30s, default: matches --timeout)
# @env LLM_OUTPUT=/dev/stdout The output path

# ═══════════════════════════════════════════════════════════════════════════════
# NEON COLOR PALETTE - Extended Glow Effects
# ═══════════════════════════════════════════════════════════════════════════════
NEON_PINK='\u001B[38;5;198m'
NEON_CYAN='\u001B[38;5;51m'
NEON_GREEN='\u001B[38;5;46m'
NEON_ORANGE='\u001B[38;5;202m'
NEON_PURPLE='\u001B[38;5;129m'
NEON_YELLOW='\u001B[38;5;226m'
NEON_RED='\u001B[38;5;196m'
NEON_BLUE='\u001B[38;5;33m'
NEON_MAGENTA='\u001B[38;5;201m'
NEON_LIME='\u001B[38;5;82m'

# Glow variants (bold + color)
GLOW_PINK="${NEON_PINK}\u001B[1m"
GLOW_CYAN="${NEON_CYAN}\u001B[1m"
GLOW_GREEN="${NEON_GREEN}\u001B[1m"
GLOW_RED="${NEON_RED}\u001B[1m"
GLOW_YELLOW="${NEON_YELLOW}\u001B[1m"
GLOW_PURPLE="${NEON_PURPLE}\u001B[1m"

RESET='\u001B[0m'
BOLD='\u001B[1m'

# Box drawing characters
BOX_TL='╭' BOX_TR='╮' BOX_BL='╰' BOX_BR='╯'
BOX_V='│' BOX_H='─' BOX_LT='├' BOX_RT='┤'

# Initialize globals securely for set -u
tmp_exit_file=""
tmp_output_file=""
tmp_dir=""

# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

# 1. Improved Cleanup Trap (signal-safe, uses temp directory, handles HUP)
cleanup() {
    [[ -n "${tmp_exit_file:-}" && -f "${tmp_exit_file}" ]] && rm -f "$tmp_exit_file"
    [[ -n "${tmp_output_file:-}" && -f "${tmp_output_file}" ]] && rm -f "$tmp_output_file"
    [[ -n "${tmp_dir:-}" && -d "${tmp_dir}" ]] && rm -rf "$tmp_dir"
}
trap cleanup EXIT INT TERM HUP

# 2. Fixed duration_to_seconds - properly handles decimals with bc
duration_to_seconds() {
    local raw="${1:-0}"
    [[ "$raw" =~ ^([0-9.]+)([sSmMhHdD]?)$ ]] || { echo "0"; return 0; }
    local n="${BASH_REMATCH[1]}" s="${BASH_REMATCH[2],,}"
    
    if command -v bc >/dev/null 2>&1; then
        case "$s" in
            m) echo "scale=6; $n * 60" | bc -l 2>/dev/null | sed 's/.$//' ;;
            h) echo "scale=6; $n * 3600" | bc -l 2>/dev/null | sed 's/.$//' ;;
            d) echo "scale=6; $n * 86400" | bc -l 2>/dev/null | sed 's/.$//' ;;
            *) echo "$n" | bc -l 2>/dev/null | sed 's/.$//' ;;
        esac
    else
        case "$s" in
            m) printf "%.0f" "$(echo "$n * 60" | awk '{print $1}')" ;;
            h) printf "%.0f" "$(echo "$n * 3600" | awk '{print $1}')" ;;
            d) printf "%.0f" "$(echo "$n * 86400" | awk '{print $1}')" ;;
            *) printf "%.0f" "$n" ;;
        esac
    fi
}

# 3. Fixed inject_curl_timeouts - properly detects curl at command start
inject_curl_timeouts() {
    local cmd="$1" ct="$2" mt="$3"
    
    if [[ "$cmd" =~ ^[[:space:]]*curl ]]; then
        local flags=""
        [[ "$cmd" != *"--connect-timeout"* ]] && flags+=" --connect-timeout $ct"
        [[ "$cmd" != *"--max-time"* ]]        && flags+=" --max-time $mt"
        [[ "$cmd" != *"--retry"* ]]           && flags+=" --retry 3 --retry-delay 2"
        if [[ "$cmd" != *"--silent"* ]] && [[ "$cmd" != *" -s "* ]] && [[ "$cmd" != *" -s"* ]]; then
            flags+=" --silent"
        fi
        
        local curl_cmd="curl"
        if command -v /usr/bin/curl >/dev/null 2>&1; then
            curl_cmd="/usr/bin/curl"
        elif command -v /data/data/com.termux/files/usr/bin/curl >/dev/null 2>&1; then
            curl_cmd="/data/data/com.termux/files/usr/bin/curl"
        fi

        cmd="$(echo "$cmd" | sed "s/^[[:space:]]*curl/$curl_cmd$flags/")"
    elif [[ "$cmd" =~ ^[[:space:]]*wget ]] && [[ "$cmd" != *"--timeout"* ]]; then
        cmd="$(echo "$cmd" | sed "s/^([[:space:]]*wget)/\u0001 --timeout=$mt --no-verbose/")"
    fi
    echo "$cmd"
}

# 4. Fixed now_ms - proper macOS/BSD fallback
now_ms() {
    local n
    if n=$(date +%s%3N 2>/dev/null) && [[ "$n" =~ ^[0-9]+$ ]]; then
        echo "$n"
    elif [[ "$(uname)" == "Darwin" ]]; then
        if command -v python3 >/dev/null 2>&1; then
            python3 -c 'import time; print(int(time.time() * 1000))'
        else
            echo $(($(date +%s) * 1000))
        fi
    elif [[ "$(uname)" == *"BSD"* ]]; then
        if command -v python3 >/dev/null 2>&1; then
            python3 -c 'import time; print(int(time.time() * 1000))'
        else
            echo $(($(date +%s) * 1000))
        fi
    else
        echo $(( $(date +%s) * 1000 ))
    fi
}

# 5. Get terminal width - dynamic calculation
get_width() {
    tput cols 2>/dev/null || echo 60
}

# 6. Fixed get_cmd_icon - more specific regex patterns
get_cmd_icon() {
    local cmd="$1"
    if [[ "$cmd" =~ ^(git|hg|svn)[[:space:]] ]]; then echo "📦"
    elif [[ "$cmd" =~ ^(npm|yarn|pnpm|apt|apt-get|yum|dnf|pacman|brew)[[:space:]] ]]; then echo "📦"
    elif [[ "$cmd" =~ ^(curl|wget)[[:space:]] ]]; then echo "🌐"
    elif [[ "$cmd" =~ ^(python[0-9]*|node|ruby|perl|php)[[:space:]] ]]; then echo "🐍"
    elif [[ "$cmd" =~ ^(docker|kubectl|helm)[[:space:]] ]]; then echo "🐳"
    elif [[ "$cmd" =~ ^(ls|cd|pwd|mkdir|rm|cp|mv|touch|cat|grep|find|awk|sed)[[:space:]] ]]; then echo "📁"
    elif [[ "$cmd" =~ ^(ffmpeg|convert|ffprobe)[[:space:]] ]]; then echo "🎬"
    elif [[ "$cmd" =~ ^(ffuf|gobuster|nmap|nikto)[[:space:]] ]]; then echo "🔍"
    elif [[ "$cmd" =~ ^(ssh|scp|rsync|sftp)[[:space:]] ]]; then echo "🔐"
    elif [[ "$cmd" =~ ^(systemctl|service|journalctl)[[:space:]] ]]; then echo "⚙️"
    else echo "⚡"
    fi
}

# 8. Format timestamp
get_timestamp() {
    date '+%H:%M:%S'
}

# 9. Interpret exit codes
interpret_exit_code() {
    local code="$1"
    case "$code" in
        0)   echo "Success" ;;
        1)   echo "General error (often catch-all)" ;;
        2)   echo "Misuse of shell builtins or file/permission error" ;;
        124) echo "Command timed out" ;;
        126) echo "Command invoked cannot execute (Permission denied)" ;;
        127) echo "Command not found" ;;
        128) echo "Invalid argument to exit" ;;
        130) echo "Script terminated by Control-C" ;;
        137) echo "Command killed (SIGKILL)" ;;
        139) echo "Segmentation fault" ;;
        141) echo "Broken pipe" ;;
        143) echo "Command terminated (SIGTERM)" ;;
        *)
            if [[ "$code" -gt 128 ]]; then
                echo "Fatal error signal $((code - 128))"
            else
                echo "Unknown error"
            fi
            ;;
    esac
}

# 10. Cross-platform timeout wrapper
run_with_timeout() {
    local timeout_val="$1"
    shift
    local cmd="$*"
    
    if [[ -z "$timeout_val" ]]; then
        bash -c "$cmd"
        return $?
    fi
    
    if ! command -v timeout >/dev/null 2>&1; then
        bash -c "$cmd"
        return $?
    fi
    
    local timeout_sec
    timeout_sec=$(duration_to_seconds "$timeout_val")
    
    if timeout --version 2>/dev/null | grep -qi gnu; then
        timeout --preserve-status "$timeout_sec" bash -c "$cmd"
        return $?
    else
        timeout "$timeout_sec" bash -c "$cmd" 2>/dev/null
        local exit_code=$?
        if [[ $exit_code -eq 124 || $exit_code -eq 143 ]]; then
            return 124
        fi
        return $exit_code
    fi
}

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EXECUTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

tmp_dir="$(mktemp -d 2>/dev/null || mkdir -p /tmp/shell_exec_$$ && echo "/tmp/shell_exec_$$")"
tmp_exit_file="$tmp_dir/exit_code"
tmp_output_file="$tmp_dir/output"

CMD_TO_RUN=""
TIMEOUT_ARG=""
CONNECT_TIMEOUT="10s"
MAX_TIME_ARG=""
LLM_OUTPUT_PATH="${LLM_OUTPUT:-/dev/stdout}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --command) CMD_TO_RUN="$2"; shift 2 ;;
        --timeout) TIMEOUT_ARG="$2"; shift 2 ;;
        --connect-timeout) CONNECT_TIMEOUT="$2"; shift 2 ;;
        --max-time) MAX_TIME_ARG="$2"; shift 2 ;;
        *) shift ;;
    esac
done

# Validation fallback check - Redirected error warning explicitly into LLM_OUTPUT_PATH
if [[ -z "$CMD_TO_RUN" ]]; then
    echo -e "${GLOW_RED}Error: --command flag is required.${RESET}" >&2
    echo -e "${GLOW_RED}Error: --command flag is required.${RESET}" > "$LLM_OUTPUT_PATH"
    exit 1
fi

MAX_TIME_ARG="${MAX_TIME_ARG:-$TIMEOUT_ARG}"
PROCESSED_CMD=$(inject_curl_timeouts "$CMD_TO_RUN" "$CONNECT_TIMEOUT" "$MAX_TIME_ARG")
ICON=$(get_cmd_icon "$PROCESSED_CMD")
WIDTH=$(get_width)

# Visual header printing - Unified destination target
{
    printf "${GLOW_CYAN}%s" "$BOX_TL"
    printf "%${WIDTH}s" "" | tr ' ' "$BOX_H"
    printf "%s${RESET}
" "$BOX_TR"
    echo -e "${BOX_V} ${ICON} ${BOLD}Executing Command:${RESET} ${NEON_YELLOW}${PROCESSED_CMD}${RESET}"
    printf "${GLOW_CYAN}%s" "$BOX_BL"
    printf "%${WIDTH}s" "" | tr ' ' "$BOX_H"
    printf "%s${RESET}
" "$BOX_BR"
} > "$LLM_OUTPUT_PATH"

# Process Execution - Captures stderr 2>&1 inside tmp asset
START_TIME=$(now_ms)
echo "" > "$tmp_output_file"
set +u
run_with_timeout "$TIMEOUT_ARG" "$PROCESSED_CMD" > "$tmp_output_file" 2>&1
EXIT_CODE=$?
set -u
END_TIME=$(now_ms)
ELAPSED_MS=$((END_TIME - START_TIME))

# Append command execution results to output asset
cat "$tmp_output_file" >> "$LLM_OUTPUT_PATH"

# Append final analytics and error diagnostic summaries - Unified routing to LLM_OUTPUT_PATH
{
    echo -e "
${GLOW_PURPLE}─── Execution Analytics ───${RESET}"
    echo -e "⏱️ ${BOLD}Duration:${RESET} ${NEON_LIME}${ELAPSED_MS}ms${RESET}"
    if [[ $EXIT_CODE -eq 0 ]]; then
        echo -e "✅ ${BOLD}Exit Status:${RESET} ${NEON_GREEN}${EXIT_CODE} ($(interpret_exit_code $EXIT_CODE))${RESET}"
    else
        echo -e "❌ ${BOLD}Exit Status:${RESET} ${NEON_RED}${EXIT_CODE} ($(interpret_exit_code $EXIT_CODE))${RESET}"
    fi
} > "$LLM_OUTPUT_PATH"
# Ensure temporary directory exists and create required files
mkdir -p "$tmp_dir"
touch "$tmp_dir/exit_code" "$tmp_dir/output"
mkdir -p "$tmp_dir"
touch "$tmp_dir/exit_code" "$tmp_dir/output"