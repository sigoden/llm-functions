#!/usr/bin/env bash
set -e

# @describe Send a desktop notification to the user. Use this when a long-running task is complete.
# @option --title! The summary/title of the notification.
# @option --message! The body text of the notification.
# @option --urgency[low|normal|critical] The urgency level (default: normal).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local urgency="${argc_urgency:-normal}"
    
    if command -v notify-send &> /dev/null; then
        notify-send -u "$urgency" "$argc_title" "$argc_message"
        echo "Notification sent." >> "$LLM_OUTPUT"
    elif command -v osascript &> /dev/null; then
        # Fallback for macOS
        osascript -e "display notification \"$argc_message\" with title \"$argc_title\""
        echo "Notification sent." >> "$LLM_OUTPUT"
    else
        echo "Error: notify-send not found." >> "$LLM_OUTPUT"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
