#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Get the current weather in a given location.
# @option --location! The city and optionally the state or country, e.g., "London", "San Francisco, CA".

# @env LLM_OUTPUT=/dev/fd/1 The output path

main() {
    # Removed '?format=4' to allow wttr.in to output its default, potentially colored, response.
    curl -fsSL "https://wttr.in/$(echo "$argc_location" | sed 's| |+|g')&M" \
    >&1
}

eval "$(argc --argc-eval "$0" "$@")"
