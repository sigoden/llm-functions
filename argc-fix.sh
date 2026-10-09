#!/usr/bin/env bash
# @describe Fixes and formats existing Bash tool scripts to adhere to the standard argc template.
#
# This script parses a given Bash tool script, extracts its argc directives (# @...),
# its custom logic, and reconstructs the script using a standard template. This ensures
# consistency and proper integration with the argc framework. It fixes buildability issues
# like missing eval lines, incorrect shebangs, or misplaced directives.
#
# Examples:
#   ./fix-argc-tool.sh path/to/my_tool.sh
#   ./fix-argc-tool.sh path/to/my_tool.sh --output-dir ./fixed_tools/
#   ./fix-argc-tool.sh path/to/my_tool.sh --dry-run
#
# @option --output-dir <path> Directory to place the fixed tool script. Overwrites original if omitted.
# @flag --dry-run Show what changes would be made without modifying files.
# @arg tool_file! The path to the Bash tool script to fix.

set -eo pipefail

# --- Dependencies ---
REQUIRED_COMMANDS=("argc" "awk" "mktemp" "chmod" "cat")

# --- ANSI Colors ---
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
RESET='\033[0m'

# --- Utility Functions ---
_die() {
    echo -e "${RED}Error: $*${RESET}" >&2
    exit 1
}

check_dependencies() {
    for cmd in "${REQUIRED_COMMANDS[@]}"; do
        if ! command -v "$cmd" &> /dev/null; then
            _die "Required command '$cmd' not found. Please ensure it is installed and in your PATH."
        fi
    done
}

declare -a TMP_FILES
tmp_cleanup() {
    for tmp_file in "${TMP_FILES[@]}"; do
        if [[ -f "$tmp_file" ]]; then
            rm -f "$tmp_file"
        fi
    done
}
# Set up a trap to clear temporary files on exit, interrupt, or termination
trap tmp_cleanup EXIT INT TERM

mktemp_secure() {
    local tmp_file
    tmp_file=$(mktemp) || _die "Failed to create temporary file."
    TMP_FILES+=("$tmp_file")
    echo "$tmp_file"
}

# --- Main Logic ---
main() {
    check_dependencies

    # Validate argument presence
    if [[ $# -lt 1 ]]; then
        _die "Usage: $0 <tool_file>"
    fi

    if ! command -v argc &> /dev/null; then
        _die "'argc' command not found. Please ensure it is installed and in your PATH."
    fi
    # Evaluate argc arguments
    eval "$(argc --argc-eval "$0" "$@")"

    local tool_file="$argc_tool_file"
    local output_dir="${argc_output_dir:-}"
    local dry_run="${argc_dry_run:-}"

    if [[ ! -f "$tool_file" ]]; then
        _die "Tool file not found at '$tool_file'."
    fi

    local fixed_tool_path="$tool_file"
    if [[ -n "$output_dir" ]]; then
        mkdir -p "$output_dir"
        fixed_tool_path="$output_dir/$(basename "$tool_file")"
    fi

    # --- Extraction Phase ---
    local shebang="#!/usr/bin/env bash"
    local set_cmd="set -eo pipefail"
    local directives=""
    local custom_code=""

    # Read the file line by line to categorize its contents
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}" # Remove carriage returns (Windows compat)

        if [[ "$line" =~ ^[[:space:]]*#![[:space:]]*/ ]]; then
            continue # Skip existing Shebangs
        elif [[ "$line" =~ ^[[:space:]]*set[[:space:]]+-[a-zA-Z]+ ]]; then
            continue # Skip existing `set` commands
        elif [[ "$line" =~ ^[[:space:]]*eval[[:space:]]+\"\$\(argc[[:space:]]+--argc-eval ]]; then
            continue # Skip existing `eval argc` lines
        elif [[ "$line" =~ ^[[:space:]]*#[[:space:]]*@ ]]; then
            # Capture all argc directives into one block
            directives+="${line}"$'\n'
        else
            # Everything else belongs to the custom code block
            custom_code+="${line}"$'\n'
        fi
    done < "$tool_file"

    # Trim leading and trailing empty lines from custom_code using awk
    custom_code=$(printf "%s" "$custom_code" | awk '
        /^[[:space:]]*$/ {
            if (content_started) trailing_newlines = trailing_newlines "\n"
            next
        }
        {
            if (!content_started) { content_started = 1; trailing_newlines = "" }
            printf "%s%s\n", trailing_newlines, $0
            trailing_newlines = ""
        }
    ')

    # --- Reconstruction Phase ---
    local temp_fixed_script
    temp_fixed_script=$(mktemp_secure)

    # Reassemble the file strictly following the tool.md standards
    {
        echo "$shebang"
        echo "$set_cmd"
        echo ""

        if [[ -n "$directives" ]]; then
            echo -ne "$directives"
            echo ""
        else
            echo "# @describe Your Tool Description Here"
            echo ""
        fi

        if [[ -n "$custom_code" ]]; then
            echo -ne "$custom_code\n"
        else
            cat <<-'EOF'
main() {
    echo "Placeholder for custom tool logic."
    # Your custom logic goes here.
}
EOF
        fi

        echo ""
        echo 'eval "$(argc --argc-eval "$0" "$@")"'
    } > "$temp_fixed_script"

    # --- Apply Changes ---
    if [[ "$dry_run" == "1" || "$dry_run" == "true" ]]; then
        echo -e "${YELLOW}[DRY-RUN]${RESET} Tool file '$tool_file' would be formatted to standard template."
        echo "--- Proposed new content for '$fixed_tool_path': ---"
        cat "$temp_fixed_script"
        echo "----------------------------------------------------"
        return
    fi

    # Atomically place the new file
    mv "$temp_fixed_script" "$fixed_tool_path"
    chmod +x "$fixed_tool_path"

    echo -e "${GREEN}Successfully formatted '$tool_file' to standard template.${RESET}"
    echo "Fixed script saved to: '$fixed_tool_path'"
}

main "$@"
