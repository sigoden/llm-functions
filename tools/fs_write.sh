#!/data/data/com.termux/files/usr/bin/env bash
# @describe Write, append, insert, or replace content in a file
# @option --path! The path of the file to write to
# @option --contents The contents to write/append/replace. Use '-' to read from stdin
# @flag --append Append the contents to the end of the file instead of overwriting
# @option --line The specific line number to replace with the contents
# @option --insert Insert the contents *before* a specific line number
# @flag --backup Create a backup of the file before modifying
# @flag --parents Create parent directories if they don't exist
# @option --match Regex pattern to perform global replacement on the file content
# @flag --verbose Enable verbose output
# @flag --dry-run Prevents actual file modification
# @flag --force Skip all confirmation prompts
# @option --encoding Specify file encoding (ascii, utf-8, latin1)
# @option --timeout Timeout in seconds for file operations
# @option --retries Number of retries on failure
# @flag --preserve-permissions Preserve file permissions when modifying
# @flag --no-trailing-newline Do not add trailing newline to content
# @option --format Format file after operation (none, json, xml, yaml)
# @flag --in-place Edit file in place without temporary file
# @option --context Show context lines around changes (number of lines)
# @flag --diff Show diff of changes
# @flag --sudo Use sudo for file operations
# @option --chown Change ownership of file (user:group)
# @option --chmod Change permissions of file (octal)
# @option --acl Set ACL on file
# @flag --follow-symlinks Follow symlinks instead of replacing them
# @flag --ignore-backup-errors Continue even if backup fails
# @flag --json-output Output clean JSON
# @env LLM_OUTPUT=/dev/fd/1 The path where output messages should be written
# @env LLM_OUTPUT_COLOR=1 Whether to use colors in output (default: 1)

set -eo pipefail
ROOT_DIR="${LLM_ROOT_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"}"

if [[ -f "$ROOT_DIR/utils/error.sh" ]]; then
    source "$ROOT_DIR/utils/error.sh"
else
    echo "Error: Utility script '$ROOT_DIR/utils/error.sh' not found. Cannot proceed." >&2
    exit 1
fi

# Define ANSI color codes
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

# --- Utility Functions ---

declare -a TMP_FILES
tmp_cleanup() {
    for tmp_file in "${TMP_FILES[@]:-}"; do
        [[ -f "$tmp_file" ]] && rm -f "$tmp_file"
    done
}
trap tmp_cleanup EXIT INT TERM

mktemp_secure() {
    local tmp_file
    # Ensure temporary files are created in a writable directory within the workspace
    local temp_dir="/data/data/com.termux/files/home/.gemini/tmp/home/"
    mkdir -p "$temp_dir" || error_exit "Failed to create tool temp directory: $temp_dir"
    tmp_file=$(mktemp "$temp_dir/tmp.XXXXXXXXXX") || error_exit "Failed to create temporary file in $temp_dir."
    TMP_FILES+=("$tmp_file")
    echo "$tmp_file"
}

check_file_lock() {
    local file="$1"
    local timeout="${2:-30}"
    local waited=0
    while [[ -f "${file}.lock" ]] && [[ "$waited" -lt "$timeout" ]]; do
        sleep 1
        waited=$((waited + 1))
    done
    [[ -f "${file}.lock" ]] && error_exit "Timeout waiting for file lock on '$file'"
    echo "$$" > "${file}.lock"
}

release_file_lock() {
    local file="$1"
    [[ -f "${file}.lock" ]] && [[ "$(cat "${file}.lock")" == "$$" ]] && rm -f "${file}.lock"
}

detect_file_format() {
    local file="$1"
    local ext="${file##*.}"
    case "${ext,,}" in
        json) echo "json" ;;
        xml|html|svg) echo "xml" ;;
        yaml|yml) echo "yaml" ;;
        *) echo "none" ;;
    esac
}

validate_encoding() {
    local file="$1"
    local encoding="${2:-utf-8}"
    if [[ -f "$file" ]]; then
        local detected
        detected=$(file -b --mime-encoding "$file" 2>/dev/null || echo "unknown")
        if [[ "$detected" != "unknown" ]] && [[ "$detected" != "$encoding" ]] && [[ "${argc_force:-}" != "true" ]] && [[ "${argc_verbose:-}" == "true" ]]; then
            output_message "warning" "File encoding mismatch. Expected '$encoding', detected '$detected'." "$file"
        fi
    fi
}

format_content() {
    local content="$1"
    local format="$2"
    case "$format" in
        json)
            command -v jq &>/dev/null && echo "$content" | jq . 2>/dev/null || echo "$content"
            ;;
        xml)
            command -v xmllint &>/dev/null && echo "$content" | xmllint --format - 2>/dev/null || echo "$content"
            ;;
        yaml)
            command -v yq &>/dev/null && echo "$content" | yq eval -P 2>/dev/null || echo "$content"
            ;;
        *) echo "$content" ;;
    esac
}

escape_sed_replacement() {
    printf '%s' "$1" | sed 's|[&\\]|\\&|g'
}

escape_sed_pattern() {
    printf '%s' "$1" | sed 's|[&/]|\\&|g'
}

generate_diff() {
    local old_file="$1"
    local new_file="$2"
    if command -v diff &>/dev/null; then
        diff -u "$old_file" "$new_file" 2>/dev/null || true
    fi
}

show_context() {
    local file="$1"
    local line="$2"
    local context="${3:-3}"
    if [[ -f "$file" ]]; then
        awk -v n="$line" -v c="$context" 'NR >= n-c && NR <= n+c { printf "%s:%d: %s
", FILENAME, NR, $0 }' "$file"
    fi
}

apply_transform() {
    local source_file="$1"
    local dest_file="$2"
    local mode="$3"
    local content_file="$4"
    local line_no="${5:-}"
    local pattern="${6:-}"
    local contents_to_write=""

    [[ -n "$content_file" ]] && [[ -s "$content_file" ]] && contents_to_write=$(<"$content_file")

    case "$mode" in
        write)
            if [[ "${argc_no_trailing_newline:-}" == "true" ]]; then
                printf '%s' "$contents_to_write" > "$dest_file"
            else
                printf '%s
' "$contents_to_write" > "$dest_file"
            fi
            ;;
        append)
            cp "$source_file" "$dest_file"
            printf '%s' "$contents_to_write" >> "$dest_file"
            ;;
        line)
            awk -v n="$line_no" -v cf="$content_file" '
                NR==n { while ((getline l < cf) > 0) print l; next }
                { print }
            ' "$source_file" > "$dest_file"
            ;;
        insert)
            awk -v n="$line_no" -v cf="$content_file" '
                NR==n { while ((getline l < cf) > 0) print l }
                { print }
            ' "$source_file" > "$dest_file"
            ;;
        match)
            local esc_pat esc_rep
            esc_pat=$(escape_sed_pattern "$pattern")
            esc_rep=$(escape_sed_replacement "$contents_to_write")
            sed "s|${esc_pat}|${esc_rep}|g" "$source_file" > "$dest_file"
            ;;
        *) error_exit "Unknown mode '$mode'" ;;
    esac
}

resolve_path() {
    local path="$1"
    if [[ "${argc_follow_symlinks:-}" == "true" ]] && [[ -L "$path" ]]; then
        readlink -f "$path" 2>/dev/null || echo "$path"
    else
        echo "$path"
    fi
}

execute_with_sudo() {
    local cmd="$1"
    shift
    if [[ "${argc_sudo:-}" == "true" ]]; then
        sudo "$cmd" "$@"
    else
        "$cmd" "$@"
    fi
}

# --- Output Helper Function ---
output_message() {
    local status="$1"
    local message="$2"
    local target_path="${3:-}"
    local details_json="${4:-{}}"

    # Use $output_dest consistently, which is set to LLM_OUTPUT or /dev/stdout (likely /dev/fd/1)
    local output_dest="${LLM_OUTPUT:-/dev/stdout}"
    local is_json_output="${argc_json_output:-false}"
    local use_colors="${LLM_OUTPUT_COLOR:-1}"

    if [[ "$is_json_output" == "true" ]]; then
        jq -n 
            --arg status "$status" 
            --arg message "$message" 
            --arg path "$target_path" 
            --argjson details "$details_json" 
            '{ "status": $status, "message": $message, "path": $path, "data": $details }' > "$output_dest"
    else
        local color_code=""
        local reset_color='\033[0m'
        local prefix="✓"

        if [[ "$use_colors" == "1" ]]; then
            case "$status" in
                "success") color_code='\033[32m';;
                "info")    color_code='\033[34m'; prefix="ℹ";;
                "warning") color_code='\033[33m'; prefix="⚠️";;
                *)         ;;
            esac
        fi

        local formatted_message=""
        if [[ -n "$color_code" ]]; then
            formatted_message="${color_code}${prefix} ${message}${reset_color}"
        else
            formatted_message="${prefix} ${message}"
        fi
        # Corrected: Using $output_dest for all echo commands within output_message
        echo -e "$formatted_message" > "$output_dest"
        
        [[ -n "$target_path" ]] && echo -e "${CYAN}Path:${NC} $target_path" >> "$output_dest"
        
        if [[ "$details_json" != "{}" ]]; then
            if [[ "$use_colors" == "1" ]]; then
                echo -e "${CYAN}Details:${NC}" >> "$output_dest"
            else
                echo "Details:" >> "$output_dest"
            fi
            if jq -e . <<< "$details_json" >/dev/null 2 >&1; then
                echo "$details_json" | jq '.' >> "$output_dest"
            else
                echo "$details_json" >> "$output_dest"
            fi
        fi
    fi
}

# --- Main Logic ---
main() {
    local contents="${argc_contents:-}"
    local target="${argc_path:-}"
    local retries="${argc_retries:-${MAX_RETRIES:-3}}"
    local timeout="${argc_timeout:-30}"
    local message=""

    [[ "$contents" == "-" ]] && contents=$(cat)
    [[ -z "$target" ]] && error_exit "Error: --path is required."
    [[ -n "${argc_line:-}" && ! "$argc_line" =~ ^[1-9][0-9]*$ ]] && error_exit "Line number (--line) must be a positive integer."
    [[ -n "${argc_insert:-}" && ! "$argc_insert" =~ ^[1-9][0-9]*$ ]] && error_exit "Insert line number (--insert) must be a positive integer."
    [[ -n "${argc_line:-}" && -n "${argc_insert:-}" ]] && error_exit "Cannot use both --line (replace) and --insert (insert before) options simultaneously."
    [[ -n "${argc_line:-}" && -n "${argc_match:-}" ]] && error_exit "Cannot use both --line (replace) and --match (regex replace) options simultaneously."
    [[ -n "${argc_append:-}" && -n "${argc_line:-}" ]] && error_exit "Cannot use both --append (add to end) and --line (replace line) options simultaneously."
    [[ -n "${argc_append:-}" && -n "${argc_match:-}" ]] && error_exit "Cannot use both --append (add to end) and --match (regex replace) options simultaneously."

    if [[ -z "$contents" && -z "${argc_append:-}" && -z "${argc_insert:-}" && -z "${argc_line:-}" && -z "${argc_match:-}" ]]; then
        error_exit "Error: --contents is required unless using --append or --insert with potentially empty content."
    fi

    target=$(resolve_path "$target")
    local auto_format="none"
    if [[ -f "$target" && -z "${argc_format:-}" ]] || [[ "${argc_format:-}" == "auto" ]]; then
        auto_format=$(detect_file_format "$target")
    else
        auto_format="${argc_format:-none}"
    fi
    local encoding="${argc_encoding:-utf-8}"
    [[ -f "$target" ]] && validate_encoding "$target" "$encoding"

    local required_cmds=(sed mktemp cp mv printf cat awk)
    [[ "${argc_parents:-}" == "true" ]] && required_cmds+=(mkdir)
    [[ "$auto_format" == "json" ]] && required_cmds+=(jq)
    [[ "$auto_format" == "xml" ]] && required_cmds+=(xmllint)
    [[ "$auto_format" == "yaml" ]] && required_cmds+=(yq)
    [[ "${argc_sudo:-}" == "true" ]] && required_cmds+=(sudo)
    [[ "${argc_diff:-}" == "true" ]] && required_cmds+=(diff)
    for cmd in "${required_cmds[@]}"; do
        command -v "$cmd" &>/dev/null || error_exit "Required command/utility '$cmd' not found. Please ensure it is installed and in your PATH."
    done

    if [[ -f "$target" ]] && [[ -z "${argc_append:-}" ]] && [[ -z "${argc_match:-}" ]] && [[ -z "${argc_line:-}" ]] && [[ -z "${argc_insert:-}" ]]; then
        local tmp_diff
        tmp_diff=$(mktemp_secure)
        if [[ "${argc_no_trailing_newline:-}" == "true" ]]; then
            printf '%s' "$contents" > "$tmp_diff"
        else
            printf '%s
' "$contents" > "$tmp_diff"
        fi
        [[ "${argc_force:-}" != "true" ]] && "$ROOT_DIR/utils/guard_operation.sh" "Apply changes to '$target'?"
    elif [[ ! -f "$target" ]] && [[ -z "${argc_append:-}" ]] && [[ -z "${argc_match:-}" ]] && [[ -z "${argc_line:-}" ]] && [[ -z "${argc_insert:-}" ]]; then
        [[ "${argc_force:-}" != "true" ]] && "$ROOT_DIR/utils/guard_path.sh" "$target" "Create and write to '$target'?"
    fi

    local action_desc="modify"
    if [[ ! -f "$target" ]]; then action_desc="create and write to"; fi
    [[ "${argc_append:-}" == "true" ]] && action_desc="append to"
    [[ -n "${argc_insert:-}" ]] && action_desc="insert into"
    [[ -n "${argc_line:-}" ]] && action_desc="replace line $argc_line in"
    [[ -n "${argc_match:-}" ]] && action_desc="replace matches in"

    if [[ "${argc_dry_run:-}" == "true" || "${DRY_RUN:-0}" == "1" ]]; then
        output_message "warning" "[DRY-RUN] Would ${action_desc} '$target'" "$target"
        return
    fi

    local saved_permissions="" saved_owner="" saved_group=""
    if [[ -f "$target" ]] && ([[ "${argc_preserve_permissions:-}" == "true" ]] || [[ "${PRESERVE_PERMISSIONS:-0}" == "1" ]]); then
        saved_permissions=$(stat -c "%a" "$target" 2>/dev/null || stat -f "%OLp" "$target" 2>/dev/null || true)
        saved_owner=$(stat -c "%U" "$target" 2>/dev/null || stat -f "%Su" "$target" 2>/dev/null || true)
        saved_group=$(stat -c "%G" "$target" 2>/dev/null || stat -f "%Sg" "$target" 2>/dev/null || true)
    fi

    if [[ -f "$target" && "${argc_backup:-}" == "true" ]]; then
        local backup_file="${target}.bak"
        local backup_counter=0
        if [[ "${argc_ignore_backup_errors:-}" != "true" ]]; then
            while [[ -f "$backup_file" ]]; do
                backup_counter=$((backup_counter + 1))
                backup_file="${target}.bak.${backup_counter}"
            done
            cp "$target" "$backup_file" 2>/dev/null || error_exit "Failed to create backup of '$target'"
        else
            backup_file="${target}.bak.$(date +%s)"
            cp "$target" "$backup_file" 2>/dev/null || true
        fi
        [[ "${argc_verbose:-}" == "true" ]] && echo -e "${CYAN}Backup created:${NC} ${backup_file}" # Removed explicit redirection
    fi

    local processed_contents="$contents"
    if [[ "$auto_format" != "none" ]]; then
        processed_contents=$(format_content "$contents" "$auto_format")
    fi

    if [[ "${argc_parents:-}" == "true" ]]; then
        local target_dir
        target_dir=$(dirname "$target")
        [[ ! -d "$target_dir" ]] && mkdir -p "$target_dir" || true
    fi

    local tmp_work tmp_source tmp_output
    tmp_work=$(mktemp_secure)
    tmp_source=$(mktemp_secure)
    tmp_output=$(mktemp_secure)
    printf '%s' "$processed_contents" > "$tmp_source"

    local operation_attempt=0 operation_success=false mode="write"
    [[ "${argc_append:-}" == "true" ]] && mode="append"
    [[ -n "${argc_line:-}" ]] && mode="line"
    [[ -n "${argc_insert:-}" ]] && mode="insert"
    [[ -n "${argc_match:-}" ]] && mode="match"

    while [[ "$operation_attempt" -lt "$retries" ]] && [[ "$operation_success" == "false" ]]; do
        operation_attempt=$((operation_attempt + 1))
        [[ -f "$target" ]] && check_file_lock "$target" "$timeout"
        if [[ -f "$target" ]]; then
            cp "$target" "$tmp_work"
        else
            : > "$tmp_work"
        fi
        
        apply_transform "$tmp_work" "$tmp_output" "$mode" "$tmp_source" "${argc_line:-}" "${argc_insert:-}" "${argc_match:-}"
        operation_success=$?

        [[ -f "$target" ]] && release_file_lock "$target"
        [[ "$operation_success" != 0 && "$operation_attempt" -lt "$retries" ]] && sleep 1
    done

    [[ "$operation_success" != 0 ]] && error_exit "Failed to perform file operation on '$target' after $retries attempts."

    local old_file=""
    if [[ "${argc_diff:-}" == "true" || -n "${argc_context:-}" ]]; then
        old_file=$(mktemp_secure)
        if [[ -f "$target" ]]; then
            cp "$target" "$old_file"
        else
            : > "$old_file"
        fi
    fi

    if [[ "${argc_in_place:-}" == "true" ]]; then
        mv "$tmp_output" "$target" || error_exit "Failed to move temporary file to '$target' in-place."
    else
        mv "$tmp_output" "$target" || error_exit "Failed to move temporary file to '$target'."
    fi

    if [[ -n "${argc_chown:-}" ]]; then
        execute_with_sudo chown "${argc_chown}" "$target" 2>/dev/null || true
    fi
    if [[ -n "${argc_chmod:-}" ]]; then
        execute_with_sudo chmod "${argc_chmod}" "$target" 2>/dev/null || true
    fi
    if [[ -n "${argc_acl:-}" ]]; then
        if command -v setfacl &>/dev/null; then
            execute_with_sudo setfacl -m "${acl}" "$target" 2>/dev/null || true
        fi
    fi

    if [[ -n "$saved_permissions" ]]; then
        chmod "$saved_permissions" "$target" 2>/dev/null || true
    fi
    if [[ -n "$saved_owner" && -n "$saved_group" ]]; then
        chown "$saved_owner:$saved_group" "$target" 2>/dev/null || true
    fi

    if [[ "${argc_diff:-}" == "true" ]] && [[ -f "$old_file" ]]; then
        local diff_output
        diff_output=$(generate_diff "$old_file" "$target")
        if [[ -n "$diff_output" ]]; then
            # Removed explicit redirection, relying on default stdout capture
            [[ "${LLM_OUTPUT_COLOR:-1}" == "1" ]] && echo -e "${CYAN}--- Diff ---${NC}" || echo "--- Diff ---"
            echo "$diff_output"
        fi
    fi

    local context_line_num=""
    [[ -n "${argc_line:-}" ]] && context_line_num="$argc_line"
    [[ -n "${argc_insert:-}" ]] && context_line_num="$argc_insert"
    if [[ -n "${argc_context:-}" ]] && [[ -n "$context_line_num" ]]; then
        show_context "$target" "$context_line_num" "${argc_context}" # Removed >&1
    fi
    
    local collected_details="{}"
    local file_size_str="unknown"
    if [[ "${argc_verbose:-}" == "true" ]]; then
        local file_size
        file_size=$(wc -c < "$target" 2>/dev/null || echo "unknown")
        file_size_str="$file_size"
        collected_details=$(jq -n --arg size "$file_size" '{"file_size": $size}')
        echo -e "${CYAN}File size:${NC} ${file_size} bytes" # Removed >&1
    fi
    
    case "$mode" in
        "write") message="Contents written to: $target" ;;
        "append") message="Contents appended to: $target" ;;
        "line") message="Line $argc_line replaced in: $target" ;;
        "insert") message="Content inserted at line $argc_insert in: $target" ;;
        "match") message="Replaced all matches of '$argc_match' in: $target" ;;
        *) message="File operation completed on: $target" ;;
    esac

    output_message "success" "$message" "$target" "$collected_details"
}

eval "$(argc --argc-eval "$0" "$@")"
