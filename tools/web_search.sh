#!/data/data/com.termux/files/usr/bin/env bash
# ==============================================================================
# Web Search Utility (Pyrmethus Enhanced Edition)
# ==============================================================================
# NOTE: keep annotation lines last in this header block — argc merges any
# trailing comment lines into the final @flag/@option description.
#
# @describe Perform a web search using the You.com API backend
# @option --query! <TEXT>              Search query
# @option --limit <NUM>               Maximum results (default: 10)
# @option --include-domains <DOMAINS> Comma-separated domains to include
# @option --exclude-domains <DOMAINS> Comma-separated domains to exclude
# @option --export-format <FMT>       Output format: json|csv|md|html|table (default: json)
# @option --cache-ttl <SECONDS>       Cache TTL in seconds (default: 3600)
# @option --timeout <SECONDS>         Python call timeout in seconds (default: 30)
# @flag   --no-cache                  Bypass cache and force a live query
# @flag   --clear-cache               Delete all cached results and exit

set -euo pipefail

# ── constants ────────────────────────────────────────────────────────────────
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PYTHON_SCRIPT="${SCRIPT_DIR}/web_search.py"
readonly CACHE_DIR="${WEBSEARCH_CACHE_DIR:-${HOME}/.cache/websearch}"
readonly CONFIG_DIR="${WEBSEARCH_CONFIG_DIR:-${HOME}/.config/websearch}"
readonly LOG_FILE="${CACHE_DIR}/web_search.log"

# ── helpers ──────────────────────────────────────────────────────────────────

log() {
    local level="$1"; shift
    printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$level" "$*" \
        >> "$LOG_FILE" 2>/dev/null || true
}

die() {
    local msg="$1"
    log "ERROR" "$msg"
    printf '%s\n' "$(printf '{"success":false,"error":"%s"}' "$msg")"
    exit 1
}

# Fix 8: correct LLM_OUTPUT append — use printf with real newline
emit() {
    local content="$1"
    printf '%s\n' "$content"
    if [[ -n "${LLM_OUTPUT:-}" ]]; then
        printf '%s\n' "$content" >> "$LLM_OUTPUT"
    fi
}

# Fix 2: check cache TTL
cache_valid() {
    local file="$1"
    local ttl="$2"
    [[ -f "$file" ]] || return 1
    local mtime now age
    mtime=$(stat -c '%Y' "$file" 2>/dev/null || stat -f '%m' "$file" 2>/dev/null) || return 1
    now=$(date +%s)
    age=$(( now - mtime ))
    (( age < ttl ))
}

# ── preflight ────────────────────────────────────────────────────────────────

preflight() {
    # Fix 3: validate Python script exists
    [[ -f "$PYTHON_SCRIPT" ]] || die "Python script not found: $PYTHON_SCRIPT"
    command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH"
    mkdir -p "$CACHE_DIR" "$CONFIG_DIR"
}

# ── main ─────────────────────────────────────────────────────────────────────

main() {
    preflight

    # Handle --clear-cache
    if [[ "${argc_clear_cache:-}" == "true" ]]; then
        rm -f "${CACHE_DIR}"/*.json
        printf '{"success":true,"message":"Cache cleared"}\n'
        return 0
    fi

    local query="${argc_query}"
    local limit="${argc_limit:-10}"
    local inc="${argc_include_domains:-}"
    local exc="${argc_exclude_domains:-}"
    local fmt="${argc_export_format:-json}"
    local ttl="${argc_cache_ttl:-3600}"
    local timeout_sec="${argc_timeout:-30}"

    # Validate limit is numeric
    if ! [[ "$limit" =~ ^[0-9]+$ ]]; then
        die "Invalid --limit value: '$limit' (must be a positive integer)"
    fi

    # Validate format
    case "$fmt" in
        json|csv|md|html|table) ;;
        *) die "Invalid --export-format '$fmt'. Choose: json csv md html table" ;;
    esac

    # Build cache key from all relevant inputs
    local cache_key
    cache_key=$(printf '%s|%s|%s|%s|%s' \
        "$query" "$limit" "$inc" "$exc" "$fmt" \
        | sha256sum | cut -d' ' -f1)
    local cache_file="${CACHE_DIR}/${cache_key}.json"

    # Fix 4: reliable no-cache detection
    local use_cache=true
    [[ "${argc_no_cache:-}" == "true" ]] && use_cache=false

    # Serve from cache if valid
    if [[ "$use_cache" == "true" ]] && cache_valid "$cache_file" "$ttl"; then
        log "INFO" "Cache HIT for query='${query}'"
        emit "$(cat "$cache_file")"
        return 0
    fi

    log "INFO" "Cache MISS — querying live for '${query}'"

    # Fix 6: pass domains as separate properly-quoted arguments
    local py_args=("$query" --limit "$limit" --export-format "$fmt")
    [[ -n "$inc" ]] && py_args+=(--include-domains "$inc")
    [[ -n "$exc" ]] && py_args+=(--exclude-domains "$exc")

    # Fix 7: timeout guard on Python call
    # Fix 5: only cache on success; validate JSON before caching
    local resp exit_code=0
    resp=$(timeout "$timeout_sec" python3 "$PYTHON_SCRIPT" "${py_args[@]}" 2>>"$LOG_FILE") \
        || exit_code=$?

    if (( exit_code == 124 )); then
        die "Python search timed out after ${timeout_sec}s"
    fi

    if (( exit_code != 0 )); then
        log "ERROR" "Python exited $exit_code for query='${query}'"
        # Still emit whatever we got (may be a JSON error envelope)
        emit "${resp:-$(printf '{"success":false,"error":"Search failed (exit %s)"}' "$exit_code")}"
        return 1
    fi

    # Validate JSON before caching
    if ! printf '%s' "$resp" | python3 -c "import sys,json; json.load(sys.stdin)" \
            >/dev/null 2>&1; then
        log "WARN" "Response is not valid JSON — not caching"
        emit "$resp"
        return 0
    fi

    # Cache and emit
    printf '%s\n' "$resp" > "$cache_file"
    log "INFO" "Cached result to ${cache_file}"
    emit "$resp"
}

eval "$(argc --argc-eval "$0" "$@")"
main "$@"
