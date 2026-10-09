#!/data/data/com.termux/files/usr/bin/env bash
set -eo pipefail

# @describe Manage AIChat sessions with persistent context (tool-based)
# @option --action![start|save|load|list|delete] Action
# @option --session Session name
# @option --context Session context data

cleanup() {
    local exit_code=$?
    exit $exit_code
}

trap cleanup EXIT

main() {
    local root_dir="${LLM_ROOT_DIR:-.}"
    local session_dir="$root_dir/sessions"
    mkdir -p "$session_dir"
    
    case "$argc_action" in
        "start") start_session "$session_dir" ;;
        "save") save_session "$session_dir" ;;
        "load") load_session "$session_dir" ;;
        "list") list_sessions "$session_dir" ;;
        "delete") delete_session "$session_dir" ;;
        *) echo "Unknown action: $argc_action" >&2; exit 1 ;;
    esac
}

start_session() {
    local session_dir="$1"
    local session="${argc_session:-default}"
    local session_file="$session_dir/${session}.json"
    
    # Initialize session
    cat > "$session_file" << EOF
{
    "name": "$session",
    "created_at": "$(date -Iseconds)",
    "messages": [],
    "context": {},
    "tools_used": []
}
EOF
    
    echo "Session started: $session" >> "${LLM_OUTPUT:-/dev/stdout}"
}

save_session() {
    local session_dir="$1"
    local session="${argc_session:-default}"
    local session_file="$session_dir/${session}.json"
    local context="${argc_context:-{}}"
    
    if [[ -f "$session_file" ]]; then
        # Use jq to update context
        jq --argjson ctx "$context" '.context += $ctx' "$session_file" > "${session_file}.tmp"
        mv "${session_file}.tmp" "$session_file"
        echo "Session saved: $session" >> "${LLM_OUTPUT:-/dev/stdout}"
    else
        echo "Session not found: $session" >&2
        exit 1
    fi
}

load_session() {
    local session_dir="$1"
    local session="${argc_session:-default}"
    local session_file="$session_dir/${session}.json"
    
    if [[ -f "$session_file" ]]; then
        jq '.context' "$session_file" >> "${LLM_OUTPUT:-/dev/stdout}"
    else
        echo "Session not found: $session" >&2
        exit 1
    fi
}

list_sessions() {
    local session_dir="$1"
    echo "Listing sessions in $session_dir:"
    for f in "$session_dir"/*.json; do
        [[ -e "$f" ]] || continue
        basename "$f" .json
    done >> "${LLM_OUTPUT:-/dev/stdout}"
}

delete_session() {
    local session_dir="$1"
    local session="${argc_session:-default}"
    local session_file="$session_dir/${session}.json"
    
    if [[ -f "$session_file" ]]; then
        rm "$session_file"
        echo "Session deleted: $session" >> "${LLM_OUTPUT:-/dev/stdout}"
    else
        echo "Session not found: $session" >&2
        exit 1
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
