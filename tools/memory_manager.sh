#!/data/data/com.termux/files/usr/bin/env bash
# @describe Manage persistent memory for AIChat conversations and context
# @option --action! Action (store|retrieve|search|clear|export|import|cleanup)
# @option --key! Memory key or identifier (required for store/retrieve/search/clear)
# @option --value! Value to store (required for store)
# @option --type! Memory type (conversation|preference|context|knowledge) (default: context)
# @option --session! Session identifier (default: default)
# @option --tags! Comma-separated tags for categorisation (optional)
# @option --days! Retention period in days for cleanup (default: 30)

set -euo pipefail

# -------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------
LLM_ROOT_DIR="${LLM_ROOT_DIR:-$(pwd)}"
MEMORY_DIR="${LLM_ROOT_DIR}/memory"
mkdir -p "${MEMORY_DIR}"

# -------------------------------------------------------------------------
# Argument Parsing (Manual)
# -------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --action) action="$2"; shift 2 ;;
            --key) key="$2"; shift 2 ;;
            --value) value="$2"; shift 2 ;;
            --type) type="$2"; shift 2 ;;
            --session) session="$2"; shift 2 ;;
            --tags) tags="$2"; shift 2 ;;
            --days) days="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
}

parse_args "$@"

# Set defaults
argc_action="${action:-}"
argc_key="${key:-}"
argc_value="${value:-}"
argc_type="${type:-context}"
argc_session="${session:-default}"
argc_tags="${tags:-}"
argc_days="${days:-30}"

# -------------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------------
die() {
    local msg="${1:-Unknown error}"
    local code="${2:-1}"
    echo "ERROR: ${msg}" >&2
    exit "${code}"
}

# -------------------------------------------------------------------------
# Core actions
# -------------------------------------------------------------------------
case "${argc_action}" in
    store)
        [[ -n "${argc_key:-}" ]] || die "Missing --key"
        [[ -n "${argc_value:-}" ]] || die "Missing --value"

        timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        entry=$(jq -n \
            --arg key "${argc_key}" \
            --arg value "${argc_value}" \
            --arg type "${argc_type:-context}" \
            --arg session "${argc_session:-default}" \
            --arg tags "$(echo "${argc_tags:-}" | tr ' ' ',')" \
            --arg timestamp "${timestamp}" \
            '{
                key: $key,
                value: $value,
                type: $type,
                session: $session,
                tags: ($tags | split(",")),
                timestamp: $timestamp
            }')

        echo "${entry}" >> "${MEMORY_DIR}/${argc_type}.jsonl"
        echo "Memory stored: ${argc_key}" >&2
        ;;

    retrieve)
        [[ -n "${argc_key:-}" ]] || die "Missing --key"
        type_val="${argc_type:-context}"
        file_val="${MEMORY_DIR}/${type_val}.jsonl"
        [[ -f "${file_val}" ]] || die "No memory file for type '${type_val}'"
        jq -r --arg key "${argc_key}" 'select(.key == $key) | .value' "${file_val}" ;;

    search)
        [[ -n "${argc_value:-}" ]] || die "Missing --value (query)"
        type_val="${argc_type:-context}"
        file_val="${MEMORY_DIR}/${type_val}.jsonl"
        [[ -f "${file_val}" ]] || die "No memory file for type '${type_val}'"
        query_val="${argc_value}"
        jq -r --arg query "${query_val}" \
            'select((.value | test($query; "i")) or (.tags[] | test($query; "i")))' "${file_val}" ;;

    clear)
        type_val="${argc_type:-context}"
        file_val="${MEMORY_DIR}/${type_val}.jsonl"
        [[ -f "${file_val}" ]] && > "${file_val}" && echo "Memory cleared for type '${type_val}'" ;;

    export)
        type_val="${argc_type:-context}"
        file_val="${MEMORY_DIR}/${type_val}.jsonl"
        [[ -f "${file_val}" ]] || die "No memory to export for type '${type_val}'"
        export_file="${MEMORY_DIR}/${type_val}_export_$(date +%Y%m%d_%H%M%S).json"
        jq -s '.' "${file_val}" > "${export_file}"
        echo "Memory exported to: ${export_file}" ;;

    import)
        [[ -n "${argc_value:-}" ]] || die "Missing --value (file to import)"
        import_file="${argc_value}"
        [[ -f "${import_file}" ]] || die "Import file not found: ${import_file}"
        type_val="${argc_type:-context}"
        file_val="${MEMORY_DIR}/${type_val}.jsonl"
        while IFS= read -r line; do
            echo "${line}" | base64 -d >> "${file_val}"
        done < <(base64 -w0 "${import_file}")
        echo "Memory imported from: ${import_file}" ;;

    cleanup)
        days_val="${argc_days:-30}"
        cutoff=$(date -u -d "-${days_val} days" +"%Y-%m-%dT%H:%M:%SZ")
        for f in "${MEMORY_DIR}"/*.jsonl; do
            [[ -f "${f}" ]] && jq --arg cutoff "${cutoff}" 'select(.timestamp >= $cutoff)' "${f}" > "${f}.tmp" && mv "${f}.tmp" "${f}"
        done
        echo "Cleanup complete – retained memories newer than ${days_val} days" ;;

    *)
        die "Unknown action: ${argc_action}" 1 ;;
esac
