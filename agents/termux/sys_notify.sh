#!/data/data/com.termux/files/usr/bin/bash
# sys_notify.sh - Send system status notifications via Telegram
# Requires: curl, termux-api (for battery, wifi, etc.)

set -euo pipefail

# Configuration - Set these via environment variables or edit directly
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
NOTIFY_INTERVAL="${NOTIFY_INTERVAL:-300}"  # 5 minutes default

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $*"
}

error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

success() {
    echo -e "${GREEN}[OK]${NC} $*"
}

# Check dependencies
check_deps() {
    local missing=()
    for cmd in curl termux-battery-status termux-wifi-connectioninfo termux-telephony-deviceinfo; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing dependencies: ${missing[*]}"
        error "Install with: pkg install termux-api curl"
        return 1
    fi
    return 0
}

# Get system metrics
get_battery() {
    termux-battery-status 2>/dev/null | jq -r '
        "Battery: \(.percentage)% (\(.status | lower))\(if .plugged != "UNPLUGGED" then ", charging" else "" end))"
    ' 2>/dev/null || echo "Battery: N/A"
}

get_memory() {
    local meminfo
    meminfo=$(cat /proc/meminfo 2>/dev/null)
    local total available
    total=$(echo "$meminfo" | awk '/MemTotal:/ {print $2}')
    available=$(echo "$meminfo" | awk '/MemAvailable:/ {print $2}')
    if [[ -n "$total" && -n "$available" ]]; then
        local used=$((total - available))
        local pct=$((used * 100 / total))
        echo "RAM: ${pct}% used ($(numfmt --to=iec --from-unit=K ${used}K) / $(numfmt --to=iec --from-unit=K ${total}K))"
    else
        echo "RAM: N/A"
    fi
}

get_storage() {
    local df_out
    df_out=$(df -h "$HOME" 2>/dev/null | tail -1)
    local avail pct
    avail=$(echo "$df_out" | awk '{print $4}')
    pct=$(echo "$df_out" | awk '{print $5}')
    echo "Storage: $pct used ($avail free on $HOME)"
}

get_cpu_temp() {
    # Try multiple thermal zones
    for zone in /sys/class/thermal/thermal_zone*/temp; do
        if [[ -r "$zone" ]]; then
            local temp
            temp=$(cat "$zone" 2>/dev/null)
            if [[ -n "$temp" && "$temp" -gt 0 ]]; then
                echo "CPU Temp: $((temp / 1000))°C"
                return
            fi
        fi
    done
    echo "CPU Temp: N/A"
}

get_network() {
    local wifi_info
    wifi_info=$(termux-wifi-connectioninfo 2>/dev/null)
    if [[ -n "$wifi_info" && "$wifi_info" != "null" ]]; then
        local ssid signal ip
        ssid=$(echo "$wifi_info" | jq -r '.ssid // "unknown"')
        signal=$(echo "$wifi_info" | jq -r '.rssi // "N/A"')
        ip=$(echo "$wifi_info" | jq -r '.ip // "N/A"')
        echo "WiFi: $ssid (${signal} dBm, $ip)"
    else
        echo "WiFi: Disconnected"
    fi
}

get_uptime() {
    local up_sec
    up_sec=$(cat /proc/uptime | awk '{print int($1)}')
    local days=$((up_sec / 86400))
    local hours=$(((up_sec % 86400) / 3600))
    local mins=$(((up_sec % 3600) / 60))
    printf "Uptime: %dd %dh %dm\n" "$days" "$hours" "$mins"
}

get_load() {
    local load
    load=$(cat /proc/loadavg | awk '{print $1", "$2", "$3}')
    echo "Load: $load (1m, 5m, 15m)"
}

get_processes() {
    local count
    count=$(ps aux | wc -l)
    echo "Processes: $((count - 1))"
}

# Build notification message
build_message() {
    local hostname
    hostname=$(getprop net.hostname 2>/dev/null || echo "termux")
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    cat <<EOF
📱 <b>Termux System Status</b>
🏷 <b>Device:</b> $hostname
🕐 <b>Time:</b> $timestamp

$(get_battery)
$(get_memory)
$(get_storage)
$(get_cpu_temp)
$(get_network)
$(get_uptime)
$(get_load)
$(get_processes)
EOF
}

# Send via Telegram
send_telegram() {
    local message="$1"
    if [[ -z "$TELEGRAM_BOT_TOKEN" || -z "$TELEGRAM_CHAT_ID" ]]; then
        warn "Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
        echo "$message"
        return 0
    fi

    local response
    response=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d parse_mode="HTML" \
        -d text="$message" \
        -d disable_web_page_preview="true")

    if echo "$response" | jq -e '.ok' >/dev/null 2>&1; then
        success "Notification sent to Telegram"
    else
        error "Failed to send Telegram notification"
        echo "$response" | jq .
        return 1
    fi
}

# Send via termux-notification (local)
send_local() {
    local message="$1"
    local title="Termux Status"
    # Strip HTML tags for local notification
    local plain_msg
    plain_msg=$(echo "$message" | sed 's/<[^>]*>//g' | head -c 1000)
    termux-notification --title "$title" --content "$plain_msg" --priority high 2>/dev/null || {
        warn "termux-notification failed (termux-api needed)"
    }
}

# Main execution
main() {
    local mode="${1:-once}"
    log "Starting sys_notify in $mode mode"

    if ! check_deps; then
        exit 1
    fi

    case "$mode" in
        once)
            local msg
            msg=$(build_message)
            send_telegram "$msg"
            send_local "$msg"
            ;;
        daemon)
            log "Running as daemon (interval: ${NOTIFY_INTERVAL}s). Press Ctrl+C to stop."
            while true; do
                local msg
                msg=$(build_message)
                send_telegram "$msg"
                send_local "$msg"
                sleep "$NOTIFY_INTERVAL"
            done
            ;;
        test)
            log "Test mode - showing message only:"
            build_message
            ;;
        *)
            error "Usage: $0 {once|daemon|test}"
            exit 1
            ;;
    esac
}

main "$@"
