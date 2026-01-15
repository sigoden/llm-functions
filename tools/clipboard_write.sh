#!/usr/bin/env bash
set -e

# @describe Write text to the system clipboard (Linux/macOS). Useful for passing code/commands to the user.
# @option --text! The text content to copy to clipboard.

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    if command -v xclip &> /dev/null; then
        echo -n "$argc_text" | xclip -selection clipboard
        echo "Success: Text copied to X11 clipboard." >> "$LLM_OUTPUT"
    elif command -v wl-copy &> /dev/null; then
        echo -n "$argc_text" | wl-copy
        echo "Success: Text copied to Wayland clipboard." >> "$LLM_OUTPUT"
    elif command -v pbcopy &> /dev/null; then
        echo -n "$argc_text" | pbcopy
        echo "Success: Text copied to macOS clipboard." >> "$LLM_OUTPUT"
    else
        echo "Error: No clipboard utility found (install xclip, wl-clipboard, or use macOS)." >> "$LLM_OUTPUT"
        exit 1
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
