#!/data/data/com.termux/files/usr/bin/env bash
set -euo pipefail

# @describe Send a Telegram message, file, or photo
# @option --token! Bot API Token
# @option --chat-id! Chat ID
# @option --message -m Message body
# @option --file -f Path to file to send
# @option --photo -p Path to photo to send

# @env LLM_OUTPUT=/dev/stdout The output path.

main() {
    local message="${argc_message:-}"
    local file="${argc_file:-}"
    local photo="${argc_photo:-}"
    
    if [[ -z "$message" && -z "$file" && -z "$photo" && ! -t 0 ]]; then
        message=$(cat)
    fi
    
    if [[ -z "$message" && -z "$file" && -z "$photo" ]]; then
        echo "{\"status\": \"error\", \"msg\": \"Message, file, or photo required\"}"
        exit 1
    fi

    local url="https://api.telegram.org/bot${argc_token}"
    
    if [[ -n "$photo" ]]; then
        url+="/sendPhoto"
        if ! curl -s -X POST "$url" \
            -F "chat_id=${argc_chat_id}" \
            -F "photo=@${photo}" \
            -F "caption=${message:-}" > /dev/null; then
            echo "{\"status\": \"error\", \"msg\": \"Failed to send photo\"}"
            exit 1
        fi
        echo "{\"status\": \"success\", \"msg\": \"Photo sent to Telegram\"}"
    elif [[ -n "$file" ]]; then
        url+="/sendDocument"
        if ! curl -s -X POST "$url" \
            -F "chat_id=${argc_chat_id}" \
            -F "document=@${file}" \
            -F "caption=${message:-}" > /dev/null; then
            echo "{\"status\": \"error\", \"msg\": \"Failed to send file\"}"
            exit 1
        fi
        echo "{\"status\": \"success\", \"msg\": \"File sent to Telegram\"}"
    else
        url+="/sendMessage"
        if ! curl -s -X POST "$url" \
            -d "chat_id=${argc_chat_id}" \
            -d "text=${message}" > /dev/null; then
            echo "{\"status\": \"error\", \"msg\": \"Failed to send message\"}"
            exit 1
        fi
        echo "{\"status\": \"success\", \"msg\": \"Message sent to Telegram\"}"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
