#!/data/data/com.termux/files/usr/bin/env bash
set -euo pipefail

# @describe Send system status notification via Telegram

# @cmd Send a system status notification
main() {
    # Get disk usage
    DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}')

    # Get battery level (requires termux-api)
    BATTERY_LEVEL=$(termux-battery-status 2>/dev/null | grep -o '"percentage": [0-9]*' | awk '{print $2}' || echo "N/A")

    # Build message
    MESSAGE="System Status:
- Disk usage: $DISK_USAGE
- Battery: ${BATTERY_LEVEL}%"

    # Send notification
    "$HOME/.config/aichat/llm-functions/tools/notify.sh" --message "$MESSAGE"
}

eval "$(argc --argc-eval "$0" "$@")"
