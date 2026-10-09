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
NEON_PINK='\033[38;5;198m'
NEON_CYAN='\033[38;5;51m'
NEON_GREEN='\033[38;5;46m'
NEON_ORANGE='\033[38;5;202m'
NEON_PURPLE='\033[38;5;129m'
NEON_YELLOW='\033[38;5;226m'
NEON_RED='\033[38;5;196m'
NEON_BLUE='\033[38;5;33m'
NEON_MAGENTA='\033[38;5;201m'
NEON_LIME='\033[38;5;82m'

# Glow variants (bold + color)
GLOW_PINK="${NEON_PINK}\033[1m"
GLOW_CYAN="${NEON_CYAN}\033[1m"
GLOW_GREEN="${NEON_GREEN}\033[1m"
GLOW_RED="${NEON_RED}\033[1m"
GLOW_YELLOW="${NEON_YELLOW}\033[1m"
GLOW_PURPLE="${NEON_PURPLE}\033[1m"

RESET='\033[0m'
BOLD='\033[1m'

# Box drawing characters
BOX_TL='╭' BOX_TR='╮' BOX_BL='╰' BOX_BR='╯'
BOX_V='│' BOX_H='─' BOX_LT='├' BOX_RT='┤'

# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

# 1. Improved Cleanup Trap (signal-safe, uses temp directory, handles HUP)
cleanup() {
    [[ -f "${tmp_exit_file:-}" ]] && rm -f "$tmp_exit_file"
    [[ -f "${tmp_output_file:-}" ]] && rm -f "$tmp_output_file"
    [[ -d "${tmp_dir:-}" ]] && rm -rf "$tmp_dir"
}
trap cleanup EXIT INT TERM HUP

# 2. Fixed duration_to_seconds - properly handles decimals with bc
duration_to_seconds() {
    local raw="${1:-0}"
    [[ "$raw" =~ ^([0-9.]+)([sSmMhHdD]?)$ ]] || { echo "0"; return 0; }
    local n="${BASH_REMATCH[1]}" s="${BASH_REMATCH[2],,}"
    
    # Use bc for decimal precision if available, otherwise fallback to integer math
    if command -v bc >/dev/null 2>&1; then
        case "$s" in
            m) echo "scale=6; $n * 60" | bc -l 2>/dev/null | sed 's/\.$//' ;;
            h) echo "scale=6; $n * 3600" | bc -l 2>/dev/null | sed 's/\.$//' ;;
            d) echo "scale=6; $n * 86400" | bc -l 2>/dev/null | sed 's/\.$//' ;;
            *) echo "$n" | bc -l 2>/dev/null | sed 's/\.$//' ;;
        esac
    else
        # Fallback: truncate decimals but handle properly
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
    
    # Check for curl at the beginning (with optional whitespace)
    if [[ "$cmd" =~ ^[[:space:]]*curl ]]; then
        local flags=""
        [[ "$cmd" != *"--connect-timeout"* ]] && flags+=" --connect-timeout $ct"
        [[ "$cmd" != *"--max-time"* ]]        && flags+=" --max-time $mt"
        [[ "$cmd" != *"--retry"* ]]           && flags+=" --retry 3 --retry-delay 2"
        # Only add silent if not already present
        if [[ "$cmd" != *"--silent"* ]] && [[ "$cmd" != *" -s "* ]] && [[ "$cmd" != *" -s"* ]]; then
            flags+=" --silent"
        fi
        
        # Ensure we use the system curl to avoid shadowing by tools
        local curl_cmd="curl"
        if command -v /usr/bin/curl >/dev/null 2>&1; then
            curl_cmd="/usr/bin/curl"
        elif command -v /data/data/com.termux/files/usr/bin/curl >/dev/null 2>&1; then
            curl_cmd="/data/data/com.termux/files/usr/bin/curl"
        fi

        # Use sed to insert flags after curl and replace with absolute path if found
        cmd="$(echo "$cmd" | sed "s/^[[:space:]]*curl/$curl_cmd$flags/")"
    elif [[ "$cmd" =~ ^[[:space:]]*wget ]] && [[ "$cmd" != *"--timeout"* ]]; then
        cmd="$(echo "$cmd" | sed "s/^\([[:space:]]*wget\)/\1 --timeout=$mt --no-verbose/")"
    fi
    echo "$cmd"
}

# 4. Fixed now_ms - proper macOS/BSD fallback
now_ms() {
    local n
    if n=$(date +%s%3N 2>/dev/null) && [[ "$n" =~ ^[0-9]+$ ]]; then
        echo "$n"
    elif [[ "$(uname)" == "Darwin" ]]; then
        # macOS: use python3 if available, otherwise estimate
        if command -v python3 >/dev/null 2>&1; then
            python3 -c 'import time; print(int(time.time() * 1000))'
        else
            # Rough fallback - may have second-level precision
            echo $(($(date +%s) * 1000))
        fi
    elif [[ "$(uname)" == *"BSD"* ]]; then
        # BSD variants
        if command -v python3 >/dev/null 2>&1; then
            python3 -c 'import time; print(int(time.time() * 1000))'
        else
            echo $(($(date +%s) * 1000))
        fi
    else
        # Linux fallback
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
    # Use word boundary matching for more accuracy
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
    local cmd="$@"
    
    if [[ -z "$timeout_val" ]]; then
        bash -c "$cmd"
        return $?
    fi
    
    # Check if timeout command exists
    if ! command -v timeout >/dev/null 2>&1; then
        # No timeout available - just run directly
        bash -c "$cmd"
        return $?
    fi
    
    local timeout_sec
    timeout_sec=$(duration_to_seconds "$timeout_val")
    
    # Check for GNU timeout (has --preserve-status)
    if timeout --version 2>/dev/null | grep -qi gnu; then
        timeout --preserve-status "$timeout_sec" bash -c "$cmd"
    else
        # macOS/BSD timeout (different flags)
        # macOS: timeout [seconds] command
        timeout "$timeout_sec" bash -c "$cmd" 2>/dev/null || {
            local exit_code=$?
            # On macOS, timeout returns 126 if command timed out
            # We need to capture the actual exit code differently
            if [[ $exit_code -eq 126 ]]; then
                # Command timed out - use gtimeout from coreutils or implement manually
                # For now, return 124 (standard timeout exit code)
                return 124
            fi
            return $exit_code
        }
    fi
}

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════
main() {
    local start_ms; start_ms="$(now_ms)"
    local cmd="${argc_command:?}"
    local out="${LLM_OUTPUT:-/dev/stdout}"
    local width; width=$(get_width)

    # ═══════════════════════════════════════════════════════════════════════════
    # PATH SANITIZATION
    # ═══════════════════════════════════════════════════════════════════════════
    # Remove any llm-functions/bin from PATH to avoid recursive shadowing.
    # AIChat tools in that directory expect JSON and will fail when called as shell commands.
    local CLEAN_PATH=""
    local OLD_IFS="$IFS"
    IFS=':'
    for p in $PATH; do
        if [[ "$p" != *"/llm-functions/bin" ]]; then
            CLEAN_PATH="${CLEAN_PATH}${CLEAN_PATH:+:}$p"
        fi
    done
    IFS="$OLD_IFS"
    export PATH="$CLEAN_PATH"
    
    # Secure temporary file generation with unique directory
    tmp_dir="$(mktemp -d)"
    tmp_exit_file="${tmp_dir}/cmd_exit.$$"
    tmp_output_file="${tmp_dir}/cmd_out.$$"

    # Resolve timeouts
    local ct_s; ct_s="$(duration_to_seconds "${argc_connect_timeout:-10s}")"
    local mt_s; mt_s="$(duration_to_seconds "${argc_max_time:-${argc_timeout:-30s}}")"
    cmd="$(inject_curl_timeouts "$cmd" "$ct_s" "$mt_s")"

    # ═══════════════════════════════════════════════════════════════════════════
    # TTY-aware Header and UI - NEON STYLE (Dynamic Width)
    # ═══════════════════════════════════════════════════════════════════════════
    if [[ -t 1 ]]; then
        local icon; icon=$(get_cmd_icon "$cmd")
        local timestamp; timestamp=$(get_timestamp)
        
        # Dynamic border width (subtract for box chars and padding)
        local border_width=$((width - 4))
        [[ $border_width -lt 10 ]] && border_width=20
        
        # Top border with glow
        local border
        border=$(printf '%*s' "$border_width" '' | tr ' ' '─')
        printf "${NEON_PURPLE}${BOX_TL}${border}${BOX_TR}${RESET}\n"
        
        # Header line
        printf "${NEON_PINK} ${icon} ${GLOW_CYAN}[EXEC]${RESET} ${NEON_YELLOW}›${RESET} "
        printf "${BOLD}%s${RESET}\n" "$cmd"
        
        # Info bar
        printf "${NEON_PURPLE}${BOX_V}${RESET} ${NEON_CYAN}Time:${RESET} ${NEON_YELLOW}%s${RESET}  " "$timestamp"
        printf "${NEON_CYAN}Timeout:${RESET} ${NEON_ORANGE}%s${RESET}\n" "${argc_timeout:-30s}"
        
        # Separator
        printf "${NEON_PURPLE}${BOX_LT}${border}${BOX_RT}${RESET}\n"
    fi

    set +e
    
    # Robust execution with proper exit code capture
    local exit_code=0
    if [[ -n "${argc_timeout:-}" ]]; then
        run_with_timeout "${argc_timeout}" "$cmd" >"$tmp_output_file" 2>&1
        exit_code=$?
    else
        bash -c "$cmd" >"$tmp_output_file" 2>&1
        exit_code=$?
    fi
    
    # Ensure exit code is always captured (handle edge cases)
    echo "$exit_code" >"$tmp_exit_file"
    exit_code=$(<"$tmp_exit_file")
    [[ -z "$exit_code" ]] && exit_code=0
    
    # ═══════════════════════════════════════════════════════════════════════════
    # OUTPUT AND ERROR REPORTING
    # ═══════════════════════════════════════════════════════════════════════════
    
    # 1. Prepare hints for shadowing
    local hint=""
    if grep -q "invalid JSON data" "$tmp_output_file"; then
        hint="⚠️  HINT: Command output contains 'invalid JSON data'. This often means a system command (like curl) is being shadowed by an AIChat tool symlink.\nTry using 'command <cmd>' or absolute path to bypass shadowing."
    fi

    # 2. Write to terminal (if applicable)
    if [[ -t 1 ]]; then
        if [[ -n "$hint" ]]; then
            printf "${NEON_RED}%b${RESET}\n" "$hint"
            printf "${NEON_PURPLE}${BOX_V}${RESET} "
        fi

        if [[ -s "$tmp_output_file" ]]; then
            # Print with prefix for better UI
            sed "s/^/${NEON_PURPLE}${BOX_V}${RESET} /" "$tmp_output_file"
        else
            printf "${NEON_PURPLE}${BOX_V}${RESET} ${NEON_YELLOW}Command finished with exit code %d (no output).${RESET}\n" "$exit_code"
        fi
    fi

    # 3. Write to LLM_OUTPUT (clean version)
    if [[ "$out" != "/dev/stdout" && "$out" != "/dev/stderr" && "$out" != "&1" && "$out" != "&2" ]]; then
        # Write hint if found
        if [[ -n "$hint" ]]; then
            printf "%b\n\n" "$hint" >> "$out"
        fi

        # Write actual output
        if [[ -s "$tmp_output_file" ]]; then
            cat "$tmp_output_file" >> "$out"
        else
            printf "Command finished with exit code %d (no output).\n" "$exit_code" >> "$out"
        fi

        # Write structured error report
        if [[ "$exit_code" -ne 0 ]]; then
            local desc; desc=$(interpret_exit_code "$exit_code")
            printf "\n--- ERROR REPORT ---\nEXIT CODE: %d (%s)\n" "$exit_code" "$desc" >> "$out"
        fi
    else
        # Fallback for direct stdout/stderr redirection
        if [[ -n "$hint" ]]; then
            printf "%b\n\n" "$hint"
        fi
        if [[ -s "$tmp_output_file" ]]; then
            cat "$tmp_output_file"
        fi
    fi

    # Performance Metric & Semantic Footer - NEON STYLE (TTY ONLY)
    local dur; dur=$(( $(now_ms) - start_ms ))
    if [[ -t 1 ]]; then
        local border_width=$((width - 4))
        [[ $border_width -lt 10 ]] && border_width=20
        local border
        border=$(printf '%*s' "$border_width" '' | tr ' ' '─')
        
        # Separator
        printf "${NEON_PURPLE}${BOX_LT}${border}${BOX_RT}${RESET}\n"
        
        # Status line
        [[ "$exit_code" -eq 0 ]] && status_color="$NEON_GREEN" || status_color="$NEON_RED"
        [[ "$exit_code" -eq 0 ]] && symbol="✓" || symbol="✗"
        [[ "$exit_code" -eq 0 ]] && status_text="SUCCESS" || status_text="FAILED"
        
        printf "${NEON_PURPLE}${BOX_V}${RESET} ${status_color}%s${RESET} " "$symbol"
        printf "${status_color}%s${RESET} " "$status_text"
        printf "${NEON_CYAN}Duration:${RESET} ${NEON_LIME}%dms${RESET}  " "$dur"
        printf "${NEON_CYAN}Exit:${RESET} ${status_color}%d${RESET}" "$exit_code"
        
        if [[ "$exit_code" -ne 0 ]]; then
            local desc; desc=$(interpret_exit_code "$exit_code")
            printf " (${NEON_YELLOW}%s${RESET})" "$desc"
        fi
        printf "\n"
        
        # Bottom border
        printf "${NEON_PURPLE}${BOX_BL}${border}${BOX_BR}${RESET}\n"
    elif [[ "$exit_code" -ne 0 ]]; then
        # Simple non-TTY failure summary
        local desc; desc=$(interpret_exit_code "$exit_code")
        printf "✗ FAILED: Exit %d (%s) (Duration: %dms)\n" "$exit_code" "$desc" "$dur" >&2
    fi

    return "$exit_code"
}

eval "$(argc --argc-eval "$0" "$@")"
