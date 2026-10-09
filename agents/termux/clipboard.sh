#!/data/data/com.termux/files/usr/bin/bash
# clipboard.sh - Get or set system clipboard
# Requires: termux-api (termux-clipboard-get, termux-clipboard-set)

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
Usage: clipboard.sh [OPTIONS] [TEXT]

Get or set system clipboard content.

Options:
  -g, --get             Get clipboard content (default if no args)
  -s, --set TEXT        Set clipboard content
  -c, --clear           Clear clipboard
  -a, --append TEXT     Append to clipboard
  -p, --prepend TEXT    Prepend to clipboard
  -j, --json            Output as JSON
  -v, --verbose         Verbose output
  -h, --help            Show this help
  --version             Show version

Examples:
  clipboard.sh                    # Get clipboard
  clipboard.sh -g                 # Get clipboard (explicit)
  clipboard.sh -s "Hello World"   # Set clipboard
  clipboard.sh -a " appended"     # Append to clipboard
  clipboard.sh -p "prepended "    # Prepend to clipboard
  clipboard.sh -c                 # Clear clipboard
  clipboard.sh -j                 # Get clipboard as JSON

Note: Requires termux-api package (pkg install termux-api)
EOF
}

log() {
    local level="$1"
    shift
    local msg="$*"
    case "$level" in
        ERROR) echo -e "${RED}[ERROR]${NC} $msg" >&2 ;;
        WARN)  echo -e "${YELLOW}[WARN]${NC} $msg" >&2 ;;
        INFO)  echo -e "${BLUE}[INFO]${NC} $msg" ;;
        DEBUG) [[ "$VERBOSE" == "true" ]] && echo -e "${CYAN}[DEBUG]${NC} $msg" ;;
    esac
}

check_termux_api() {
    if ! command -v termux-clipboard-get >/dev/null 2>&1; then
        log "ERROR" "termux-clipboard-get not found"
        log "INFO" "Install with: pkg install termux-api"
        return 1
    fi
    if ! command -v termux-clipboard-set >/dev/null 2>&1; then
        log "ERROR" "termux-clipboard-set not found"
        log "INFO" "Install with: pkg install termux-api"
        return 1
    fi
    return 0
}

get_clipboard() {
    local json="$1"
    local content
    content=$(termux-clipboard-get 2>/dev/null) || {
        log "ERROR" "Failed to get clipboard"
        return 1
    }

    if [[ "$json" == "true" ]]; then
        jq -n --arg content "$content" '{content: $content, length: ($content|length), timestamp: now|todateiso8601}'
    else
        echo "$content"
    fi
}

set_clipboard() {
    local text="$1"
    local json="$2"

    termux-clipboard-set "$text" 2>/dev/null || {
        log "ERROR" "Failed to set clipboard"
        return 1
    }

    if [[ "$json" == "true" ]]; then
        jq -n --arg content "$text" '{content: $content, length: ($content|length), action: "set", timestamp: now|todateiso8601}'
    else:
        log "INFO" "Clipboard set (${#text} chars)"
    fi
}

clear_clipboard() {
    local json="$1"

    termux-clipboard-set "" 2>/dev/null || {
        log "ERROR" "Failed to clear clipboard"
        return 1
    }

    if [[ "$json" == "true" ]]; then
        jq -n '{content: "", length: 0, action: "clear", timestamp: now|todateiso8601}'
    else
        log "INFO" "Clipboard cleared"
    fi
}

append_clipboard() {
    local text="$1"
    local json="$2"

    local current
    current=$(termux-clipboard-get 2>/dev/null) || {
        log "ERROR" "Failed to get current clipboard"
        return 1
    }

    local new_content="${current}${text}"
    termux-clipboard-set "$new_content" 2>/dev/null || {
        log "ERROR" "Failed to set clipboard"
        return 1
    }

    if [[ "$json" == "true" ]]; then
        jq -n --arg content "$new_content" --arg appended "$text" '{content: $content, length: ($content|length), appended: $appended, action: "append", timestamp: now|todateiso8601}'
    else
        log "INFO" "Appended to clipboard (${#text} chars, total: ${#new_content})"
    fi
}

prepend_clipboard() {
    local text="$1"
    local json="$2"

    local current
    current=$(termux-clipboard-get 2>/dev/null) || {
        log "ERROR" "Failed to get current clipboard"
        return 1
    }

    local new_content="${text}${current}"
    termux-clipboard-set "$new_content" 2>/dev/null || {
        log "ERROR" "Failed to set clipboard"
        return 1
    }

    if [[ "$json" == "true" ]]; then
        jq -n --arg content "$new_content" --arg prepended "$text" '{content: $content, length: ($content|length), prepended: $prepended, action: "prepend", timestamp: now|todateiso8601}'
    else
        log "INFO" "Prepended to clipboard (${#text} chars, total: ${#new_content})"
    fi
}

main() {
    local action="get"
    local text=""
    local json="false"
    local verbose="false"

    VERBOSE="$verbose"

    # Check termux-api
    if ! check_termux_api; then
        exit 1
    fi

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            -g|--get)
                action="get"
                shift
                ;;
            -s|--set)
                action="set"
                text="$2"
                shift 2
                ;;
            -c|--clear)
                action="clear"
                shift
                ;;
            -a|--append)
                action="append"
                text="$2"
                shift 2
                ;;
            -p|--prepend)
                action="prepend"
                text="$2"
                shift 2
                ;;
            -j|--json)
                json="true"
                shift
                ;;
            -v|--verbose)
                verbose="true"
                VERBOSE="true"
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            --version)
                echo "clipboard.sh v$VERSION"
                exit 0
                ;;
            *)
                # If no action specified yet, treat as set
                if [[ "$action" == "get" && -z "$text" ]]; then
                    action="set"
                    text="$1"
                else
                    log "ERROR" "Unknown option: $1"
                    show_help
                    exit 1
                fi
                shift
                ;;
        esac
    done

    # Execute action
    case "$action" in
        get)
            get_clipboard "$json"
            ;;
        set)
            if [[ -z "$text" ]]; then
                log "ERROR" "Text required for set action"
                exit 1
            fi
            set_clipboard "$text" "$json"
            ;;
        clear)
            clear_clipboard "$json"
            ;;
        append)
            if [[ -z "$text" ]]; then
                log "ERROR" "Text required for append action"
                exit 1
            fi
            append_clipboard "$text" "$json"
            ;;
        prepend)
            if [[ -z "$text" ]]; then
                log "ERROR" "Text required for prepend action"
                exit 1
            fi
            prepend_clipboard "$text" "$json"
            ;;
    esac
}

main "$@"
