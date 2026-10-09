#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Remove the file or directory at the specified path.

# @option --path! The path of the file or directory to remove

# @env LLM_OUTPUT=/dev/fd/1 The output path
# @env DRY_RUN=0 If set to 1, prevents actual file removal

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
source "$ROOT_DIR/utils/error.sh"

main() {
    if [[ ! -e "$argc_path" ]]; then
        log_error "Path does not exist: $argc_path"
        return
    fi

    if [[ "$DRY_RUN" == "1" ]]; then
        echo "[DRY-RUN] Would remove: '$argc_path'" >&1
        return
    fi

    if [[ -f "$argc_path" || -d "$argc_path" ]]; then
        "$ROOT_DIR/utils/guard_path.sh" "$argc_path" "Remove '$argc_path'?"
        rm -rf "$argc_path"
    fi
    
    message="Path removed: $argc_path"
    if [[ "$LLM_OUTPUT_COLOR" == "1" ]]; then
        echo -e "\033[32m$message\033[0m" >&1 # Green color for success
    else
        echo "$message" >&1
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
