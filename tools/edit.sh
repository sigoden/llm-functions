#!/data/data/com.termux/files/usr/bin/env bash
# =============================================================================
# edit.sh - File editing utility for LLM Functions
# =============================================================================
# @describe Edit, create, read, or patch files with various operations
# @option --path! <STRING> [Target file path (required)]
# @option --operation! <STRING> [Operation: write|append|prepend|insert|replace|delete|read|patch|rename|copy|move|touch|truncate|head|tail|search|sort|reverse|duplicate|merge|diff|wc|chmod|unique]
# @option --content <STRING> [Content to write/append/prepend/insert]
# @option --line <INT> [Line number for insert/delete/replace]
# @option --end-line <INT> [End line number for range delete/replace]
# @option --pattern <STRING> [Search/replace regex pattern]
# @option --replacement <STRING> [Replacement string]
# @option --dest <STRING> [Destination path for copy/move/rename]
# @option --count <INT> [Number of lines for head/tail (default: 10); 1/2/3 for wc]
# @option --source <STRING> [Source file for merge/diff operation]
# @option --mode <STRING> [Permission mode for chmod (e.g., 755, u+w)]
# @flag --create-dirs [Create parent directories if needed]
# @flag --backup [Create .bak backup before modifying]
# @flag --dry-run [Show what would be done without doing it]
# @flag --force [Overwrite existing files]
# @flag --global [Apply pattern replacement globally]
# @flag --case-insensitive [Case-insensitive pattern matching]
# @flag --literal [Treat pattern as literal string]
# @flag --verbose [Enable verbose output]
# @flag --json-output [Output clean JSON]
# @flag --follow-symlinks [Follow symbolic links]
# @flag --no-final-newline [Don't add final newline]
# @flag --invert-match [Invert grep pattern match]
# @flag --line-number [Show line numbers in output]
# @flag --count-only [Only show match count (for search)]
# @env LLM_OUTPUT=/dev/stdout [Output destination]
# @env DRY_RUN=0 [If set to 1, prevents actual file modification]
# @env DEBUG=false [Enable debug output]

set -euo pipefail
shopt -s nullglob

# =============================================================================
# CONFIGURATION
# =============================================================================
readonly SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
readonly SCRIPT_VERSION="2.9.0"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly TIMEOUT_SEC="${TOOL_TIMEOUT:-30}"
readonly CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/aichat/tools"
DEBUG="${DEBUG:-false}"

# =============================================================================
# COLOR & LOGGING
# =============================================================================
# FIX: Do NOT declare these readonly — they must be reassignable in JSON mode
_RED='\033[0;31m'
_GREEN='\033[0;32m'
_YELLOW='\033[1;33m'
_BLUE='\033[0;34m'
_MAGENTA='\033[0;35m'
_CYAN='\033[0;36m'
_BRIGHT_RED='\033[1;31m'
_BRIGHT_GREEN='\033[1;32m'
_NC='\033[0m'

# Logging functions reference mutable color variables
error()   { echo -e "${_RED}✗ $*${_NC}" >&2; }
success() { echo -e "${_GREEN}✓ $*${_NC}"; }
info()    { echo -e "${_BLUE}ℹ $*${_NC}"; }
warn()    { echo -e "${_YELLOW}⚠ $*${_NC}"; }
debug()   { [[ "$DEBUG" == "true" ]] && echo -e "${_CYAN}🔧 $*${_NC}" >&2 || true; }

# =============================================================================
# ERROR HANDLING
# =============================================================================
EXIT_SUCCESS=0
EXIT_GENERAL_ERROR=1
EXIT_INVALID_INPUT=2
EXIT_FILE_NOT_FOUND=3
EXIT_PERMISSION_DENIED=4
EXIT_NETWORK_ERROR=5
EXIT_TIMEOUT=124
EXIT_COMMAND_NOT_FOUND=127

# Track temp files/dirs for cleanup
_tmp_files=()
_tmp_dirs=()

trap '_cleanup_all $EXIT_CODE' EXIT INT TERM

# Store exit code for trap
_EXIT_CODE() { EXIT_CODE=$?; }
trap '_EXIT_CODE' EXIT

_cleanup_all() {
    local f
    for f in "${_tmp_files[@]:-}"; do
        [[ -n "$f" && -f "$f" ]] && rm -f "$f"
    done
    for f in "${_tmp_dirs[@]:-}"; do
        [[ -n "$f" && -d "$f" ]] && rm -rf "$f"
    done
}

die() {
    local msg="$1"
    local exit_code="${2:-$EXIT_GENERAL_ERROR}"
    error "$msg"
    exit "$exit_code"
}

# =============================================================================
# UTILITIES
# =============================================================================
require_cmd() {
    command -v "$1" &>/dev/null || die "Required command not found: $1" $EXIT_COMMAND_NOT_FOUND
}

require_file() {
    [[ -f "$1" ]] || die "Required file not found: $1" $EXIT_FILE_NOT_FOUND
}

require_file_or_empty() {
    # OK if it's a regular file or does not exist at all
    [[ -f "$1" ]] && return 0
    [[ ! -e "$1" ]] && return 0
    die "Path exists but is not a regular file: $1" $EXIT_INVALID_INPUT
}

require_readable() {
    require_file "$1"
    [[ -r "$1" ]] || die "File is not readable: $1" $EXIT_PERMISSION_DENIED
}

require_writable() {
    if [[ -e "$1" ]]; then
        [[ -w "$1" ]] || die "File is not writable: $1" $EXIT_PERMISSION_DENIED
    else
        local parent
        parent="$(dirname "$1")"
        # Normalize '.' to actual cwd for writability check
        [[ "$parent" == "." ]] && parent="$(pwd)"
        [[ -w "$parent" ]] || die "Parent directory is not writable: $parent" $EXIT_PERMISSION_DENIED
    fi
}

# =============================================================================
# JSON HELPERS
# =============================================================================
# FIX: Properly formatted json_output using jq for safe encoding
json_output() {
    local status="$1"
    local message="${2:-}"
    local data="${3:-null}"
    jq -n \
        --arg status  "$status" \
        --arg message "$message" \
        --argjson data "$data" \
        '{status: $status, message: $message, data: $data}'
}

# FIX: json_error uses integer code, prints to stderr, exits cleanly
json_error() {
    local code="${1:-1}"
    local message="${2:-Unknown error}"
    # Use jq for safe JSON encoding of the message
    printf '{"status":"error","code":%d,"message":%s}\n' \
        "$code" \
        "$(jq -Rn --arg m "$message" '$m')" >&2
    exit "$code"
}

json_encode() {
    jq -Rs '.' <<<"$1"
}

# =============================================================================
# RETRY
# =============================================================================
retry_cmd() {
    local max_retries="${1:-3}"
    local delay="${2:-1}"
    local attempt=1
    shift 2

    while (( attempt <= max_retries )); do
        debug "Attempt $attempt/$max_retries: $*"
        if "$@"; then
            return 0
        fi
        if (( attempt < max_retries )); then
            sleep $(( delay * attempt ))
        fi
        (( attempt++ )) || true
    done
    return 1
}

# =============================================================================
# ATOMIC WRITE  (single definition)
# =============================================================================
# FIX: Removed duplicate definition; renamed internal calls to _atomic_write
# which delegates here for clarity
_atomic_write() {
    local tmp="$1"
    local dest="$2"
    mv -f "$tmp" "$dest" \
        || { rm -f "$tmp"; die "Failed to finalize write to: $dest" $EXIT_GENERAL_ERROR; }
}

# =============================================================================
# CACHING
# =============================================================================
cache_get() {
    local key="$1"
    local ttl="${2:-3600}"
    local cache_file="$CACHE_DIR/$key"

    if [[ -f "$cache_file" ]]; then
        local mod_time now age
        # Portable stat: try GNU then BSD style
        mod_time=$(stat -c%Y "$cache_file" 2>/dev/null \
                   || stat -f%m "$cache_file" 2>/dev/null \
                   || echo 0)
        now=$(date +%s)
        age=$(( now - mod_time ))
        if (( age < ttl )); then
            cat "$cache_file"
            return 0
        fi
    fi
    return 1
}

cache_set() {
    local key="$1"
    local content="$2"
    mkdir -p "$CACHE_DIR"
    printf '%s\n' "$content" > "$CACHE_DIR/$key"
}

# =============================================================================
# VALIDATION HELPERS
# =============================================================================
validate_not_empty() {
    local value="$1" name="$2"
    [[ -n "$value" && "$value" != "null" ]] \
        || die "$name cannot be empty" $EXIT_INVALID_INPUT
}

validate_regex() {
    local value="$1" pattern="$2" name="$3"
    [[ $value =~ $pattern ]] \
        || die "$name format invalid: $value" $EXIT_INVALID_INPUT
}

validate_choice() {
    local value="$1" name="$2"
    shift 2
    local choice
    for choice in "$@"; do
        [[ "$value" == "$choice" ]] && return 0
    done
    die "$name must be one of: $*" $EXIT_INVALID_INPUT
}

# FIX: Separate validators — one for general integers, one for positive only
validate_integer() {
    local value="$1" name="$2"
    [[ "$value" =~ ^-?[0-9]+$ ]] \
        || die "$name must be an integer: $value" $EXIT_INVALID_INPUT
}

validate_positive_integer() {
    local value="$1" name="$2"
    [[ "$value" =~ ^[1-9][0-9]*$ ]] \
        || die "$name must be a positive integer (>=1): $value" $EXIT_INVALID_INPUT
}

validate_file_exists() {
    local path="$1" name="${2:-File}"
    [[ -f "$path" ]] || die "$name not found: $path" $EXIT_FILE_NOT_FOUND
}

validate_dir_exists() {
    local path="$1" name="${2:-Directory}"
    [[ -d "$path" ]] || die "$name not found: $path" $EXIT_FILE_NOT_FOUND
}

# =============================================================================
# PATH HELPERS
# =============================================================================
# FIX: Rewritten _canonicalize_path for correctness with edge cases
_canonicalize_path() {
    local path="$1"
    local -a parts result_parts=()
    local part

    # Expand leading ~ to HOME
    [[ "$path" == "~"* ]] && path="${HOME}${path:1}"

    # Make relative paths absolute
    [[ "$path" != /* ]] && path="$(pwd)/$path"

    # Split on /
    IFS='/' read -ra parts <<< "$path"

    for part in "${parts[@]}"; do
        case "$part" in
            ""|".")
                # Skip empty segments and current-dir references
                ;;
            "..")
                # Pop last component if possible
                if (( ${#result_parts[@]} > 0 )); then
                    unset 'result_parts[${#result_parts[@]}-1]'
                fi
                ;;
            *)
                result_parts+=("$part")
                ;;
        esac
    done

    # Reconstruct path
    local result="/"
    local segment
    for segment in "${result_parts[@]:-}"; do
        result="${result%/}/${segment}"
    done

    printf '%s\n' "${result:-/}"
}

_resolve_dest() {
    local d="$1"
    _canonicalize_path "$d"
}

_check_dest_exists() {
    local dest_path="$1"
    [[ -d "$dest_path" ]] \
        && die "Destination path is a directory: $dest_path" $EXIT_INVALID_INPUT
    if [[ -e "$dest_path" && "${argc_force:-false}" != "true" ]]; then
        die "Destination already exists: $dest_path (use --force to overwrite)" $EXIT_INVALID_INPUT
    fi
    return 0
}

_ensure_parent_dir() {
    local target_path="$1"
    local parent
    parent="$(dirname "$target_path")"

    # Normalize '.' → actual cwd
    [[ "$parent" == "." ]] && parent="$(pwd)"

    [[ -d "$parent" ]] && return 0

    if [[ "${argc_create_dirs:-false}" == "true" ]]; then
        debug "Creating parent directory: $parent"
        _is_dry_run && return 0
        mkdir -p "$parent" \
            || die "Cannot create directory: $parent" $EXIT_PERMISSION_DENIED
    else
        die "Parent directory does not exist: $parent (use --create-dirs)" $EXIT_FILE_NOT_FOUND
    fi
}

_is_dry_run() {
    [[ "${DRY_RUN:-0}" == "1" || "${argc_dry_run:-false}" == "true" ]]
}

# =============================================================================
# FILE HELPERS
# =============================================================================
_is_binary() {
    local file="$1"
    if command -v file &>/dev/null; then
        file -b "$file" 2>/dev/null | grep -qiE 'binary|executable|ELF' && return 0
    fi
    # Fallback: check for null bytes in first 8KB
    od -An -tx1 -v -N 8192 "$file" 2>/dev/null \
        | awk '/[[:space:]]00([[:space:]]|$)/{found=1; exit} END{exit !found}'
}

_make_backup() {
    local filepath="$1"
    [[ "${argc_backup:-false}" == "true" && -f "$filepath" ]] || return 0
    local bak="${filepath}.$(date +%Y%m%d_%H%M%S).bak"
    debug "Creating backup: $bak"
    _is_dry_run && return 0
    cp -p "$filepath" "$bak" \
        || die "Failed to create backup: $bak" $EXIT_GENERAL_ERROR
    [[ "${argc_json_output:-false}" != "true" ]] && info "Backup created: $bak"
}

# FIX: Track temp files in array for cleanup; avoids orphan temp files
_safe_temp() {
    local dir
    dir="$(dirname "$1")"
    local tmp

    tmp=$(mktemp "${dir}/.editfile.XXXXXX" 2>/dev/null) \
        || tmp=$(mktemp "/tmp/.editfile.XXXXXX" 2>/dev/null) \
        || die "Failed to create temporary file" $EXIT_GENERAL_ERROR

    _tmp_files+=("$tmp")
    printf '%s\n' "$tmp"
}

_last_byte() {
    # Returns the hex representation of the last byte, empty string if file empty
    tail -c 1 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n'
}

_line_count() {
    # Count lines; returns 0 for empty/nonexistent files
    local n
    n=$(grep -c '' "$1" 2>/dev/null) || n=0
    printf '%s' "$n"
}

_validate_line_number() {
    local val="$1" flag="${2:-line}"
    [[ "$val" =~ ^[1-9][0-9]*$ ]] \
        || die "--${flag} must be a positive integer >= 1, got: '$val'" $EXIT_INVALID_INPUT
}

_validate_line_range() {
    local start="$1" end="$2" total="$3"
    _validate_line_number "$start" "line"
    _validate_line_number "$end"   "end-line"
    (( end   >= start )) || die "end-line ($end) must be >= line ($start)"       $EXIT_INVALID_INPUT
    (( start <= total )) || die "Start line $start exceeds file length ($total)" $EXIT_INVALID_INPUT
    (( end   <= total )) || die "End line $end exceeds file length ($total)"     $EXIT_INVALID_INPUT
}

# =============================================================================
# AWK ENGINE
# =============================================================================
_awk_substitute() {
    local file="$1" pat="$2" repl="$3"
    local global="${4:-false}" icase="${5:-false}" literal="${6:-false}"

    # Pass pattern via environment to avoid awk quoting issues with special chars
    EDITFILE_PAT="$pat" \
    EDITFILE_REPL="$repl" \
    awk \
        -v GLOBAL="$global" \
        -v ICASE="$icase" \
        -v LITERAL="$literal" \
    '
    BEGIN {
        PAT      = ENVIRON["EDITFILE_PAT"]
        REPL     = ENVIRON["EDITFILE_REPL"]
        # Convert shell \n sequences to literal newlines in replacement
        gsub(/\\n/, "\n", REPL)
        LOW_PAT  = tolower(PAT)
        LOW_REPL = tolower(REPL)
    }

    function lit_find(haystack, needle,    h, n) {
        h = (ICASE == "true") ? tolower(haystack) : haystack
        n = (ICASE == "true") ? tolower(needle)   : needle
        return index(h, n)
    }

    function lit_replace_first(haystack, needle, repl,    pos, nlen) {
        pos  = lit_find(haystack, needle)
        nlen = length(needle)
        if (pos == 0) return haystack
        return substr(haystack, 1, pos - 1) repl substr(haystack, pos + nlen)
    }

    function lit_replace_all(haystack, needle, repl,    result, pos, nlen) {
        result = ""
        nlen   = length(needle)
        if (nlen == 0) return haystack   # Guard: empty needle → no-op
        while (1) {
            pos = lit_find(haystack, needle)
            if (pos == 0) { result = result haystack; break }
            result   = result substr(haystack, 1, pos - 1) repl
            haystack = substr(haystack, pos + nlen)
        }
        return result
    }

    function regex_replace_first(line, pat, repl,    low, tmp) {
        tmp = line
        if (ICASE == "true") {
            low = tolower(line)
            if (match(low, pat) == 0) return line
            return substr(line, 1, RSTART - 1) repl substr(line, RSTART + RLENGTH)
        }
        sub(pat, repl, tmp)
        return tmp
    }

    function regex_replace_all(line, pat, repl,    result, low, advance, tmp) {
        if (ICASE != "true") {
            tmp = line
            gsub(pat, repl, tmp)
            return tmp
        }
        result = ""
        while (length(line) > 0) {
            low = tolower(line)
            if (match(low, pat) == 0) { result = result line; break }
            result  = result substr(line, 1, RSTART - 1) repl
            advance = (RLENGTH > 0) ? RLENGTH : 1
            line    = substr(line, RSTART + advance)
        }
        return result
    }

    {
        if (LITERAL == "true") {
            if (GLOBAL == "true") print lit_replace_all(  $0, PAT, REPL)
            else                  print lit_replace_first($0, PAT, REPL)
        } else {
            if (GLOBAL == "true") print regex_replace_all(  $0, PAT, REPL)
            else                  print regex_replace_first($0, PAT, REPL)
        }
    }
    ' "$file"
}

_awk_delete_lines() {
    local file="$1" pat="$2" literal="${3:-false}" icase="${4:-false}"

    EDITFILE_PAT="$pat" \
    awk \
        -v LITERAL="$literal" \
        -v ICASE="$icase" \
    '
    BEGIN { PAT = ENVIRON["EDITFILE_PAT"]; LOW_PAT = tolower(PAT) }
    {
        matched = 0
        if (LITERAL == "true") {
            h = (ICASE == "true") ? tolower($0) : $0
            n = (ICASE == "true") ? LOW_PAT     : PAT
            matched = (index(h, n) > 0)
        } else {
            subject = (ICASE == "true") ? tolower($0) : $0
            pattern = (ICASE == "true") ? LOW_PAT     : PAT
            matched = (subject ~ pattern)
        }
        if (!matched) print
    }
    ' "$file"
}

_awk_delete_range() {
    local file="$1" start="$2" end="$3"
    awk -v S="$start" -v E="$end" 'NR < S || NR > E { print }' "$file"
}

_awk_read_range() {
    local file="$1" start="$2" end="$3" linenum="${4:-false}"
    awk \
        -v S="$start" -v E="$end" -v LINENUM="$linenum" \
    '
    NR >= S && NR <= E {
        if (LINENUM == "true") printf "%6d\t%s\n", NR, $0
        else                   print
    }
    NR > E { exit }
    ' "$file"
}

_awk_search() {
    local file="$1" pat="$2" literal="${3:-false}" icase="${4:-false}"
    local invert="${5:-false}" line_number="${6:-false}" count_only="${7:-false}"

    EDITFILE_PAT="$pat" \
    awk \
        -v LITERAL="$literal" \
        -v ICASE="$icase" \
        -v INVERT="$invert" \
        -v LINENUM="$line_number" \
        -v COUNTONLY="$count_only" \
    '
    BEGIN { PAT = ENVIRON["EDITFILE_PAT"]; LOW_PAT = tolower(PAT); count = 0 }
    {
        matched = 0
        if (LITERAL == "true") {
            h = (ICASE == "true") ? tolower($0) : $0
            n = (ICASE == "true") ? LOW_PAT     : PAT
            matched = (index(h, n) > 0)
        } else {
            subject = (ICASE == "true") ? tolower($0) : $0
            pattern = (ICASE == "true") ? LOW_PAT     : PAT
            matched = (subject ~ pattern)
        }
        if (INVERT == "true") matched = !matched
        if (matched) {
            count++
            if (COUNTONLY != "true") {
                if (LINENUM == "true") printf "%d:%s\n", NR, $0
                else                   print
            }
        }
    }
    END { if (COUNTONLY == "true") print count }
    ' "$file"
}

# =============================================================================
# OPERATIONS
# =============================================================================

# --- write ---
_op_write() {
    local filepath="$1"
    local content="${argc_content:-}"
    validate_not_empty "$content" "--content"

    _ensure_parent_dir    "$filepath"
    require_file_or_empty "$filepath"
    require_writable      "$filepath"
    _make_backup          "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would write ${#content} bytes to: $filepath"
        [[ "${argc_json_output:-false}" == "true" ]] && \
            json_output "success" "Dry run: would write" \
                "$(jq -n --arg p "$filepath" --argjson s "${#content}" '{path:$p,size:$s}')"
        return 0
    fi

    local tmp
    tmp="$(_safe_temp "$filepath")"

    if [[ "${argc_no_final_newline:-false}" == "true" ]]; then
        printf '%s'   "$content" > "$tmp" || { rm -f "$tmp"; die "Write failed" $EXIT_GENERAL_ERROR; }
    else
        printf '%s\n' "$content" > "$tmp" || { rm -f "$tmp"; die "Write failed" $EXIT_GENERAL_ERROR; }
    fi

    _atomic_write "$tmp" "$filepath"
    debug "Wrote ${#content} bytes to: $filepath"
    success "File written: $filepath"
}

# --- append ---
_op_append() {
    local filepath="$1"
    local content="${argc_content:-}"
    validate_not_empty "$content" "--content"

    _ensure_parent_dir    "$filepath"
    require_file_or_empty "$filepath"
    require_writable      "$filepath"
    _make_backup          "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would append ${#content} bytes to: $filepath"
        return 0
    fi

    # Create file if it does not exist
    [[ -f "$filepath" ]] || : > "$filepath"

    # Ensure we start on a new line if file is non-empty and lacks trailing newline
    if [[ -s "$filepath" ]]; then
        local lb
        lb="$(_last_byte "$filepath")"
        [[ -n "$lb" && "$lb" != "0a" ]] && printf '\n' >> "$filepath"
    fi

    if [[ "${argc_no_final_newline:-false}" == "true" ]]; then
        printf '%s'   "$content" >> "$filepath" || die "Append failed" $EXIT_GENERAL_ERROR
    else
        printf '%s\n' "$content" >> "$filepath" || die "Append failed" $EXIT_GENERAL_ERROR
    fi

    debug "Appended ${#content} bytes to: $filepath"
    success "Content appended to: $filepath"
}

# --- prepend ---
_op_prepend() {
    local filepath="$1"
    local content="${argc_content:-}"
    validate_not_empty "$content" "--content"

    _ensure_parent_dir    "$filepath"
    require_file_or_empty "$filepath"
    require_writable      "$filepath"
    _make_backup          "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would prepend ${#content} bytes to: $filepath"
        return 0
    fi

    if [[ ! -f "$filepath" ]]; then
        if [[ "${argc_no_final_newline:-false}" == "true" ]]; then
            printf '%s'   "$content" > "$filepath" || die "Write failed" $EXIT_GENERAL_ERROR
        else
            printf '%s\n' "$content" > "$filepath" || die "Write failed" $EXIT_GENERAL_ERROR
        fi
    else
        local tmp
        tmp="$(_safe_temp "$filepath")"
        {
            if [[ "${argc_no_final_newline:-false}" == "true" ]]; then
                printf '%s'   "$content"
            else
                printf '%s\n' "$content"
            fi
            cat "$filepath"
        } > "$tmp" || { rm -f "$tmp"; die "Prepend failed" $EXIT_GENERAL_ERROR; }
        _atomic_write "$tmp" "$filepath"
    fi

    debug "Prepended ${#content} bytes to: $filepath"
    success "Content prepended to: $filepath"
}

# --- insert ---
_op_insert() {
    local filepath="$1"
    require_file     "$filepath"
    require_writable "$filepath"

    local content="${argc_content:-}"
    validate_not_empty "$content" "--content"

    local insert_line="${argc_line:-1}"
    _validate_line_number "$insert_line" "line"

    _make_backup "$filepath"

    local total
    total="$(_line_count "$filepath")"

    # Clamp to one past end-of-file at most
    if (( insert_line > total + 1 )); then
        insert_line=$(( total + 1 ))
        debug "Clamped --line to $insert_line (EOF+1)"
    fi

    if _is_dry_run; then
        debug "DRY-RUN: would insert at line $insert_line in: $filepath"
        return 0
    fi

    local tmp
    tmp="$(_safe_temp "$filepath")"
    {
        (( insert_line > 1 )) && head -n $(( insert_line - 1 )) "$filepath" || true

        if [[ "${argc_no_final_newline:-false}" == "true" ]]; then
            printf '%s'   "$content"
        else
            printf '%s\n' "$content"
        fi

        (( insert_line <= total )) && tail -n +"$insert_line" "$filepath" || true
    } > "$tmp" || { rm -f "$tmp"; die "Insert failed" $EXIT_GENERAL_ERROR; }

    _atomic_write "$tmp" "$filepath"
    debug "Inserted at line $insert_line in: $filepath"
    success "Content inserted at line $insert_line in: $filepath"
}

# --- replace ---
_op_replace() {
    local filepath="$1"
    require_file     "$filepath"
    require_writable "$filepath"

    local content="${argc_content:-}"
    local raw_pat="${argc_pattern:-}"
    local raw_repl="${argc_replacement:-}"
    local global="${argc_global:-false}"
    local icase="${argc_case_insensitive:-false}"
    local literal="${argc_literal:-false}"

    _make_backup "$filepath"

    # --- Line-range replace ---
    if [[ -n "${argc_line:-}" ]]; then
        local start="$argc_line"
        local end="${argc_end_line:-$argc_line}"
        local total
        total="$(_line_count "$filepath")"
        _validate_line_range "$start" "$end" "$total"

        if _is_dry_run; then
            debug "DRY-RUN: would replace lines $start-$end in: $filepath"
            return 0
        fi

        local tmp
        tmp="$(_safe_temp "$filepath")"
        {
            (( start > 1 )) && head -n $(( start - 1 )) "$filepath" || true

            if [[ -n "$content" ]]; then
                if [[ "${argc_no_final_newline:-false}" == "true" ]]; then
                    printf '%s'   "$content"
                else
                    printf '%s\n' "$content"
                fi
            fi

            (( end < total )) && tail -n +$(( end + 1 )) "$filepath" || true
        } > "$tmp" || { rm -f "$tmp"; die "Line replace failed" $EXIT_GENERAL_ERROR; }

        _atomic_write "$tmp" "$filepath"
        debug "Replaced lines $start-$end in: $filepath"
        success "Lines $start-$end replaced in: $filepath"
        return 0
    fi

    # --- Pattern replace ---
    validate_not_empty "$raw_pat" "--pattern"

    if _is_dry_run; then
        debug "DRY-RUN: would replace pattern '$raw_pat' in: $filepath"
        return 0
    fi

    local tmp
    tmp="$(_safe_temp "$filepath")"
    _awk_substitute \
        "$filepath" "$raw_pat" "$raw_repl" \
        "$global" "$icase" "$literal" \
        > "$tmp" || { rm -f "$tmp"; die "Pattern replace failed" $EXIT_GENERAL_ERROR; }

    _atomic_write "$tmp" "$filepath"
    debug "Pattern replaced in: $filepath"
    success "Pattern replaced in: $filepath"
}

# --- delete ---
_op_delete() {
    local filepath="$1"
    require_file     "$filepath"
    require_writable "$filepath"
    _make_backup     "$filepath"

    # --- Line-range delete ---
    if [[ -n "${argc_line:-}" ]]; then
        local start="$argc_line"
        local end="${argc_end_line:-$argc_line}"
        local total
        total="$(_line_count "$filepath")"
        _validate_line_range "$start" "$end" "$total"

        if _is_dry_run; then
            debug "DRY-RUN: would delete lines $start-$end in: $filepath"
            return 0
        fi

        local tmp
        tmp="$(_safe_temp "$filepath")"
        _awk_delete_range "$filepath" "$start" "$end" \
            > "$tmp" || { rm -f "$tmp"; die "Line delete failed" $EXIT_GENERAL_ERROR; }

        _atomic_write "$tmp" "$filepath"
        debug "Deleted lines $start-$end in: $filepath"
        success "Lines $start-$end deleted from: $filepath"
        return 0
    fi

    # --- Pattern delete ---
    local raw_pat="${argc_pattern:-}"
    [[ -z "$raw_pat" ]] && die "--line or --pattern required for delete" $EXIT_INVALID_INPUT

    if _is_dry_run; then
        debug "DRY-RUN: would delete lines matching: $raw_pat"
        return 0
    fi

    local tmp
    tmp="$(_safe_temp "$filepath")"
    _awk_delete_lines \
        "$filepath" "$raw_pat" \
        "${argc_literal:-false}" \
        "${argc_case_insensitive:-false}" \
        > "$tmp" || { rm -f "$tmp"; die "Pattern delete failed" $EXIT_GENERAL_ERROR; }

    _atomic_write "$tmp" "$filepath"
    debug "Deleted pattern-matched lines in: $filepath"
    success "Pattern-matched lines deleted from: $filepath"
}

# --- read ---
_op_read() {
    local filepath="$1"
    require_readable "$filepath"
    _is_binary "$filepath" && warn "File appears to be binary: $filepath" || true

    local output="${LLM_OUTPUT:-/dev/stdout}"

    if [[ -n "${argc_line:-}" ]]; then
        local start="$argc_line"
        local end="${argc_end_line:-$argc_line}"
        _validate_line_number "$start" "line"
        _validate_line_number "$end"   "end-line"
        (( end >= start )) || die "end-line ($end) must be >= line ($start)" $EXIT_INVALID_INPUT
        _awk_read_range "$filepath" "$start" "$end" "${argc_line_number:-false}" >> "$output"
    else
        if [[ "${argc_line_number:-false}" == "true" ]]; then
            awk '{printf "%6d\t%s\n", NR, $0}' "$filepath" >> "$output"
        else
            cat "$filepath" >> "$output"
        fi
    fi
}

# --- head ---
_op_head() {
    local filepath="$1"
    require_readable "$filepath"
    local count="${argc_count:-10}"
    local output="${LLM_OUTPUT:-/dev/stdout}"
    validate_positive_integer "$count" "--count"
    head -n "$count" "$filepath" >> "$output"
}

# --- tail ---
_op_tail() {
    local filepath="$1"
    require_readable "$filepath"
    local count="${argc_count:-10}"
    local output="${LLM_OUTPUT:-/dev/stdout}"
    validate_positive_integer "$count" "--count"
    tail -n "$count" "$filepath" >> "$output"
}

# --- search ---
_op_search() {
    local filepath="$1"
    require_readable "$filepath"
    local pattern="${argc_pattern:-}"
    validate_not_empty "$pattern" "--pattern"

    local output="${LLM_OUTPUT:-/dev/stdout}"
    _awk_search \
        "$filepath" \
        "$pattern" \
        "${argc_literal:-false}" \
        "${argc_case_insensitive:-false}" \
        "${argc_invert_match:-false}" \
        "${argc_line_number:-false}" \
        "${argc_count_only:-false}" \
        >> "$output"
}

# --- rename ---
_op_rename() {
    local filepath="$1"
    require_file "$filepath"

    local dest="${argc_dest:-}"
    validate_not_empty "$dest" "--dest"

    dest="$(_resolve_dest "$dest")"
    _check_dest_exists "$dest"
    _ensure_parent_dir "$dest"
    _make_backup       "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would rename $filepath -> $dest"
        return 0
    fi

    mv -f "$filepath" "$dest" \
        || die "Rename failed: $filepath -> $dest" $EXIT_GENERAL_ERROR
    debug "Renamed: $filepath -> $dest"
    success "Renamed: $filepath -> $dest"
}

# --- copy ---
_op_copy() {
    local filepath="$1"
    require_file "$filepath"

    local dest="${argc_dest:-}"
    validate_not_empty "$dest" "--dest"

    dest="$(_resolve_dest "$dest")"
    _check_dest_exists "$dest"
    _ensure_parent_dir "$dest"

    if _is_dry_run; then
        debug "DRY-RUN: would copy $filepath -> $dest"
        return 0
    fi

    cp -p "$filepath" "$dest" \
        || die "Copy failed: $filepath -> $dest" $EXIT_GENERAL_ERROR
    debug "Copied: $filepath -> $dest"
    success "Copied: $filepath -> $dest"
}

# --- move (alias for rename) ---
_op_move() { _op_rename "$1"; }

# --- touch ---
_op_touch() {
    local filepath="$1"
    [[ -d "$filepath" ]] && die "Path is a directory: $filepath" $EXIT_INVALID_INPUT
    _ensure_parent_dir "$filepath"
    require_writable   "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would touch: $filepath"
        return 0
    fi

    touch "$filepath" || die "Touch failed: $filepath" $EXIT_GENERAL_ERROR
    debug "Touched: $filepath"
    success "Touched: $filepath"
}

# --- truncate ---
_op_truncate() {
    local filepath="$1"
    require_file     "$filepath"
    require_writable "$filepath"
    _make_backup     "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would truncate: $filepath"
        return 0
    fi

    : > "$filepath" || die "Truncate failed: $filepath" $EXIT_GENERAL_ERROR
    debug "Truncated: $filepath"
    success "Truncated: $filepath"
}

# --- patch ---
# FIX: Use _safe_temp (tracked array) instead of re-trapping EXIT
_op_patch() {
    local filepath="$1"
    require_file     "$filepath"
    require_writable "$filepath"
    local content="${argc_content:-}"
    validate_not_empty "$content" "--content"
    require_cmd patch

    _make_backup "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would patch: $filepath"
        return 0
    fi

    local patch_tmp
    patch_tmp="$(_safe_temp "$filepath")"   # tracked; cleaned up by _cleanup_all

    printf '%s\n' "$content" > "$patch_tmp" \
        || die "Failed to write patch content" $EXIT_GENERAL_ERROR

    patch --forward --no-backup-if-mismatch "$filepath" < "$patch_tmp" \
        || die "Patch failed: $filepath" $EXIT_GENERAL_ERROR

    debug "Patched: $filepath"
    success "Patch applied to: $filepath"
}

# --- sort ---
_op_sort() {
    local filepath="$1"
    require_file     "$filepath"
    require_writable "$filepath"
    _make_backup     "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would sort: $filepath"
        return 0
    fi

    local -a sort_args=()
    [[ "${argc_case_insensitive:-false}" == "true" ]] && sort_args+=("-f")

    local tmp
    tmp="$(_safe_temp "$filepath")"
    LC_ALL=C sort "${sort_args[@]}" "$filepath" > "$tmp" \
        || { rm -f "$tmp"; die "Sort failed" $EXIT_GENERAL_ERROR; }

    _atomic_write "$tmp" "$filepath"
    debug "Sorted: $filepath"
    success "Sorted: $filepath"
}

# --- unique ---
# FIX: sort -u and -f argument ordering clarified; -u always first
_op_unique() {
    local filepath="$1"
    require_file     "$filepath"
    require_writable "$filepath"
    _make_backup     "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would deduplicate: $filepath"
        return 0
    fi

    local -a sort_args=("-u")
    [[ "${argc_case_insensitive:-false}" == "true" ]] && sort_args+=("-f")

    local tmp
    tmp="$(_safe_temp "$filepath")"
    LC_ALL=C sort "${sort_args[@]}" "$filepath" > "$tmp" \
        || { rm -f "$tmp"; die "Unique/dedup failed" $EXIT_GENERAL_ERROR; }

    _atomic_write "$tmp" "$filepath"
    debug "Deduplicated: $filepath"
    success "Deduplicated: $filepath"
}

# --- reverse ---
_op_reverse() {
    local filepath="$1"
    require_file     "$filepath"
    require_writable "$filepath"
    _make_backup     "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would reverse: $filepath"
        return 0
    fi

    local tmp
    tmp="$(_safe_temp "$filepath")"

    if command -v tac &>/dev/null; then
        tac "$filepath" > "$tmp"
    else
        awk '{lines[NR]=$0} END{for(i=NR;i>=1;i--) print lines[i]}' "$filepath" > "$tmp"
    fi || { rm -f "$tmp"; die "Reverse failed" $EXIT_GENERAL_ERROR; }

    _atomic_write "$tmp" "$filepath"
    debug "Reversed: $filepath"
    success "Reversed: $filepath"
}

# --- duplicate (alias for copy) ---
_op_duplicate() { _op_copy "$1"; }

# --- merge ---
_op_merge() {
    local filepath="$1"
    require_file     "$filepath"
    require_writable "$filepath"

    local src_file="${argc_source:-}"
    validate_not_empty "$src_file" "--source"
    src_file="$(_resolve_dest "$src_file")"
    require_readable "$src_file"
    _make_backup "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would merge $src_file into $filepath"
        return 0
    fi

    local tmp
    tmp="$(_safe_temp "$filepath")"
    {
        cat "$filepath"
        # Ensure separation between files if target lacks trailing newline
        if [[ -s "$filepath" ]]; then
            local lb
            lb="$(_last_byte "$filepath")"
            [[ -n "$lb" && "$lb" != "0a" ]] && printf '\n'
        fi
        cat "$src_file"
    } > "$tmp" || { rm -f "$tmp"; die "Merge failed" $EXIT_GENERAL_ERROR; }

    _atomic_write "$tmp" "$filepath"
    debug "Merged $src_file into $filepath"
    success "Merged: $src_file into $filepath"
}

# --- diff ---
_op_diff() {
    local filepath="$1"
    require_readable "$filepath"

    local output="${LLM_OUTPUT:-/dev/stdout}"
    local src_file="${argc_source:-}"

    if [[ -z "$src_file" ]]; then
        if [[ -f "${filepath}.bak" ]]; then
            src_file="${filepath}.bak"
            debug "Auto-selected backup for diff: $src_file"
        else
            die "--source required for diff (no .bak file found)" $EXIT_INVALID_INPUT
        fi
    fi

    src_file="$(_resolve_dest "$src_file")"
    require_readable "$src_file"
    require_cmd diff

    local rc=0
    diff -u "$src_file" "$filepath" >> "$output" || rc=$?
    # rc=0: identical, rc=1: differences found — both acceptable
    (( rc <= 1 )) || die "diff command failed with code: $rc" $EXIT_GENERAL_ERROR
}

# --- wc ---
# FIX: byte count now uses awk to count chars portably; avoids wc -c whitespace issues
_op_wc() {
    local filepath="$1"
    require_readable "$filepath"
    local count_type="${argc_count:-1}"
    local output="${LLM_OUTPUT:-/dev/stdout}"
    validate_choice "$count_type" "--count" "1" "2" "3"

    case "$count_type" in
        1) awk 'END{print NR}'              "$filepath" >> "$output" ;;  # Lines
        2) awk '{w += NF} END{print w+0}'  "$filepath" >> "$output" ;;  # Words
        3) awk 'BEGIN{c=0}{c+=length($0)+1} END{print c}' \
               "$filepath" >> "$output" ;;                                # Bytes (approx)
    esac
}

# --- chmod ---
_op_chmod() {
    local filepath="$1"
    require_file "$filepath"

    local mode="${argc_mode:-}"
    validate_not_empty "$mode" "--mode"

    # Validate: numeric (e.g. 755, 0755) or symbolic (e.g. u+x, g-w, a=r)
    if ! [[ "$mode" =~ ^[0-7]{3,4}$ ]] && \
       ! [[ "$mode" =~ ^[ugoa]*([\+\-=][rwxXstugo]+)(,[ugoa]*([\+\-=][rwxXstugo]+))*$ ]]; then
        die "Invalid chmod mode: '$mode' (e.g. 755 or u+x,g-w)" $EXIT_INVALID_INPUT
    fi

    _make_backup "$filepath"

    if _is_dry_run; then
        debug "DRY-RUN: would chmod $mode: $filepath"
        return 0
    fi

    chmod "$mode" "$filepath" \
        || die "chmod '$mode' failed: $filepath" $EXIT_GENERAL_ERROR
    debug "chmod $mode: $filepath"
    success "Mode changed to $mode: $filepath"
}

# =============================================================================
# MAIN
# =============================================================================
main() {
    local filepath="$argc_path"
    local operation="$argc_operation"
    local json_mode="${argc_json_output:-false}"

    # Elevate debug if --verbose passed
    [[ "${argc_verbose:-false}" == "true" ]] && DEBUG="true"
    debug "Starting edit.sh v$SCRIPT_VERSION"

    # FIX: JSON mode — reassign mutable color vars (not readonly) and override log fns
    if [[ "$json_mode" == "true" ]]; then
        _RED='' _GREEN='' _YELLOW='' _BLUE='' _MAGENTA='' _CYAN=''
        _BRIGHT_RED='' _BRIGHT_GREEN='' _NC=''
        error()   { json_error $EXIT_GENERAL_ERROR "$1"; }
        success() { json_output "success" "$1"; }
        info()    { json_output "info"    "$1"; }
        warn()    { json_output "warning" "$1"; }
    fi

    [[ -z "$filepath"  ]] && die "Missing required argument: --path"      $EXIT_INVALID_INPUT
    [[ -z "$operation" ]] && die "Missing required argument: --operation" $EXIT_INVALID_INPUT

    # Whitelist of supported operations
    case "$operation" in
        write|append|prepend|insert|replace|delete|read|patch|\
        rename|copy|move|touch|truncate|head|tail|search|sort|\
        reverse|duplicate|merge|diff|wc|chmod|unique) ;;
        *) die "Unknown operation: '$operation'" $EXIT_INVALID_INPUT ;;
    esac

    # Normalize and canonicalize path
    filepath="$(_canonicalize_path "$filepath")"

    # Resolve symlinks if requested
    if [[ "${argc_follow_symlinks:-false}" == "true" && -L "$filepath" ]]; then
        filepath="$(readlink -f "$filepath")" \
            || die "Failed to resolve symlink: $filepath" $EXIT_GENERAL_ERROR
    fi

    # Dangerous-path guard
    case "$filepath" in
        /|/bin|/boot|/dev|/etc|/lib|/lib64|/proc|/root|/run|\
        /sbin|/sys|/usr|/var)
            die "Refusing to modify critical system path: $filepath" $EXIT_PERMISSION_DENIED ;;
        /etc/passwd|/etc/shadow|/etc/sudoers|/etc/sudoers.d/*)
            die "Refusing to modify sensitive system file: $filepath" $EXIT_PERMISSION_DENIED ;;
        /bin/*|/sbin/*|/usr/bin/*|/usr/sbin/*|/usr/lib/*)
            die "Refusing to modify system binary/library: $filepath" $EXIT_PERMISSION_DENIED ;;
    esac

    # Dispatch
    case "$operation" in
        write)     _op_write     "$filepath" ;;
        append)    _op_append    "$filepath" ;;
        prepend)   _op_prepend   "$filepath" ;;
        insert)    _op_insert    "$filepath" ;;
        replace)   _op_replace   "$filepath" ;;
        delete)    _op_delete    "$filepath" ;;
        read)      _op_read      "$filepath" ;;
        head)      _op_head      "$filepath" ;;
        tail)      _op_tail      "$filepath" ;;
        search)    _op_search    "$filepath" ;;
        rename)    _op_rename    "$filepath" ;;
        copy)      _op_copy      "$filepath" ;;
        move)      _op_move      "$filepath" ;;
        touch)     _op_touch     "$filepath" ;;
        truncate)  _op_truncate  "$filepath" ;;
        patch)     _op_patch     "$filepath" ;;
        sort)      _op_sort      "$filepath" ;;
        reverse)   _op_reverse   "$filepath" ;;
        duplicate) _op_duplicate "$filepath" ;;
        merge)     _op_merge     "$filepath" ;;
        diff)      _op_diff      "$filepath" ;;
        wc)        _op_wc        "$filepath" ;;
        chmod)     _op_chmod     "$filepath" ;;
        *) die "Internal error: unhandled operation: $operation" $EXIT_GENERAL_ERROR ;;
    esac
}

# =============================================================================
# ENTRY POINT
# =============================================================================
eval "$(argc --argc-eval "$0" "$@")"
