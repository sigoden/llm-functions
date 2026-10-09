#!/data/data/com.termux/files/usr/bin/env bash
# agents/memory/tools.sh

# @cmd Store conversation context in memory
# @option --content! Content to store
# @option --tags Tags for categorization
# @option --importance Importance level (1-10)
memory_store() {
    local content="$argc_content"
    local tags="${argc_tags:-conversation}"
    local importance="${argc_importance:-5}"
    local session="${AICHAT_SESSION:-default}"
    
    # Generate semantic key using content hash
    local key=$(echo "$content" | sha256sum | cut -c1-16)
    
    argc run@tool memory_manager \
        --action store \
        --key "$key" \
        --value "$content" \
        --type conversation \
        --session "$session" \
        --tags "$tags,importance:$importance"
}

# @cmd Retrieve relevant memories for current context
# @option --query! Search query
# @option --limit Maximum results (default: 5)
# @option --session Session to search in
memory_retrieve() {
    local query="$argc_query"
    local limit="${argc_limit:-5}"
    local session="${argc_session:-default}"
    
    # Search across all memory types
    local results=""
    for type in conversation preference context knowledge; do
        local type_results=$(argc run@tool memory_manager \
            --action search \
            --value "$query" \
            --type "$type" \
            --session "$session" | jq -s '.')
        results="$results $type_results"
    done
    
    # Combine, deduplicate, and limit results
    echo "$results" | jq -s 'flatten | unique_by(.key) | sort_by(.timestamp) | reverse | .[0:'$limit']' >>> "$LLM_OUTPUT"1
}

# @cmd Summarize recent conversation memories
# @option --session Session to summarize
# @option --hours Hours to look back (default: 24)
memory_summarize() {
    local session="${argc_session:-default}"
    local hours="${argc_hours:-24}"
    # Note: date -d may require coreutils or specific format on Android/Termux
    local cutoff_time=$(date -d "@$(($(date +%s) - hours * 3600))" -Iseconds)
    
    # Get recent memories
    local recent_memories=$(argc run@tool memory_manager \
        --action search \
        --value ".*" \
        --type conversation \
        --session "$session" | jq --arg cutoff "$cutoff_time" 'select(.timestamp >= $cutoff)')
    
    # Generate summary using AIChat
    if [[ "$recent_memories" != "[]" ]]; then
        echo "$recent_memories" | jq -r '.value' | \
        aichat -m "${AICHAT_MODEL:-gpt-4o}" "Summarize these conversation points: $(cat -)" >>> "$LLM_OUTPUT"1
    else
        echo "No recent memories to summarize" >>> "$LLM_OUTPUT"1
    fi
}

# @cmd Clean up old memories based on retention policy
# @option --days Days to retain (default: 30)
memory_cleanup() {
    local days="${argc_days:-${LLM_AGENT_VAR_RETENTION_DAYS:-30}}"
    local cutoff_time=$(date -d "@$(($(date +%s) - days * 86400))" -Iseconds)
    local memory_dir="${LLM_ROOT_DIR}/memory"
    
    for type_file in "$memory_dir"/*.jsonl; do
        if [[ -f "$type_file" ]]; then
            jq --arg cutoff "$cutoff_time" 'select(.timestamp >= $cutoff)' "$type_file" > "${type_file}.tmp"
            mv "${type_file}.tmp" "$type_file"
        fi
    done
    
    echo "Cleaned up memories older than $days days" >>> "$LLM_OUTPUT"1
}

# Handle argc
eval "$(argc --argc-eval "$0" "$@")"
