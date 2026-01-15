#!/usr/bin/env bash
set -e

# @describe Parse JSON data/files using jq to extract specific fields.
# @option --json! The raw JSON string or path to a JSON file (use @filepath).
# @option --query! The jq query filter (e.g., '.users[].email' or '.status').
# @flag --compact Output compact JSON (removes whitespace).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local input_content="$argc_json"
    
    # Check if input is a file path starting with @
    if [[ "$argc_json" == @* ]]; then
        local filepath="${argc_json:1}"
        if [[ -f "$filepath" ]]; then
            input_content=$(cat "$filepath")
        else
            echo "Error: File $filepath not found" >> "$LLM_OUTPUT"
            exit 1
        fi
    fi

    if [[ -n "$argc_compact" ]]; then
        echo "$input_content" | jq -c "$argc_query" >> "$LLM_OUTPUT"
    else
        echo "$input_content" | jq "$argc_query" >> "$LLM_OUTPUT"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
