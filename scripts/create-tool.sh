#!/data/data/com.termux/files/usr/bin/env bash
# ==============================================================================
# create-tool.sh — Tool Boilerplate Generator
#
# Generates boilerplate code for new tool scripts in bash, JavaScript, or Python.
# Supports various parameter types: required/optional, string/integer/boolean/array.
#
# Usage:
#   ./create-tool.sh [options] <name> [params...]
#
# Options:
#   --description <text>  Tool description
#   --force               Override existing file
#
# Arguments:
#   name                  Script filename (e.g., my_tool.sh)
#   params                Parameters (suffix: ! = required, * = optional array)
#
# Examples:
#   ./create-tool.sh my_tool.sh name! age
#   ./create-tool.sh --description "My tool" api_key! --force
#   ./create-tool.sh process.py input* output! --force
# ==============================================================================

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Supported extensions
SUPPORTED_EXTS=('sh' 'js' 'py')

# Usage function
usage() {
    head -30 "$0" | tail -25
    exit 0
}

# Error handler
die() {
    echo -e "${RED}Error:${NC} $*" >&2
    exit 1
}

# Parse arguments manually (portable alternative to argc)
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --description)
                DESCRIPTION="$2"
                shift 2
                ;;
            --force)
                FORCE=true
                shift
                ;;
            --help|-h)
                usage
                ;;
            -*)
                die "Unknown option: $1"
                ;;
            *)
                if [[ -z "${NAME:-}" ]]; then
                    NAME="$1"
                else
                    PARAMS+=("$1")
                fi
                shift
                ;;
        esac
    done
    
    [[ -n "${NAME:-}" ]] || die "Missing required argument: <name>"
}

# Main function
main() {
    local output="tools/$NAME"
    local ext="${NAME##*.}"
    
    # Validate extension
    if [[ "$ext" == "$NAME" ]]; then
        die "No extension. Supported: ${SUPPORTED_EXTS[*]}"
    fi
    
    if [[ ! " ${SUPPORTED_EXTS[*]} " =~ " $ext " ]]; then
        die "Invalid extension: $ext. Supported: ${SUPPORTED_EXTS[*]}"
    fi
    
    # Check if file exists
    if [[ -f "$output" ]] && [[ "${FORCE:-false}" != "true" ]]; then
        die "$output already exists. Use --force to override."
    fi
    
    # Set default description
    DESCRIPTION="${DESCRIPTION:-Tool description}"
    
    # Generate based on extension
    case "$ext" in
        sh) create_sh "$output" ;;
        js) create_js "$output" ;;
        py) create_py "$output" ;;
    esac
    
    chmod +x "$output"
    echo -e "${GREEN}Created:${NC} $output"
}

# Create Bash tool
create_sh() {
    local output="$1"
    
    {
        echo '#!/usr/bin/env bash'
        echo '# =============================================================================='
        echo "# $NAME — Bash Tool"
        echo '#'
        echo "# @describe $DESCRIPTION"
        echo '# @option --string! <VALUE>  Required string parameter'
        echo '# @option --string-optional <VALUE>  Optional string parameter'
        echo '# @flag --boolean  Boolean flag'
        echo '# @option --integer! <NUM>  Required integer parameter'
        echo '# @option --array* <VALUE>  Array parameter (repeatable)'
        echo '# =============================================================================='
        echo ''
        echo 'set -euo pipefail'
        echo ''
        echo 'main() {'
        echo '    echo "argc_string: ${argc_string}"'
        echo '    echo "argc_string_optional: ${argc_string_optional}"'
        echo '    echo "argc_boolean: ${argc_boolean}"'
        echo '    echo "argc_integer: ${argc_integer}"'
        echo '    echo "argc_array: ${argc_array[*]}"'
        echo '}'
        echo ''
        echo 'eval "$(argc --argc-eval "$0" "$@")"'
    } > "$output"
}

# Create JavaScript tool
create_js() {
    local output="$1"
    
    {
        echo '/**'
        echo " * $DESCRIPTION"
        echo ' * @typedef {Object} Args'
        echo ' * @property {string} string - Required string parameter'
        echo ' * @property {string} [string_optional] - Optional string parameter'
        echo ' * @property {boolean} boolean - Boolean flag'
        echo ' * @property {number} integer - Integer parameter'
        echo ' * @property {string[]} array - Array parameter'
        echo ' */'
        echo 'exports.run = function(args) {'
        echo '  const { string, string_optional, boolean, integer, array } = args;'
        echo '  '
        echo '  return {'
        echo '    string,'
        echo '    string_optional,'
        echo '    boolean,'
        echo '    integer,'
        echo '    array'
        echo '  };'
        echo '};'
    } > "$output"
}

# Create Python tool
create_py() {
    local output="$1"
    
    {
        echo '#!/usr/bin/env python3'
        echo '# =============================================================================='
        echo "# $NAME — Python Tool"
        echo '#'
        echo "# @describe $DESCRIPTION"
        echo '# @option --string! <TEXT>  Required string parameter'
        echo '# @option --string-optional <TEXT>  Optional string parameter'
        echo '# @option --boolean  Boolean flag'
        echo '# @option --integer! <NUM>  Required integer parameter'
        echo '# @option --array! <TEXT>  Array parameter (repeatable)'
        echo '# =============================================================================='
        echo ''
        echo 'from typing import List, Optional'
        echo ''
        echo ''
        echo 'def run('
        echo '    string: str,'
        echo '    string_optional: Optional[str] = None,'
        echo '    boolean: bool = False,'
        echo '    integer: int = 0,'
        echo '    array: Optional[List[str]] = None,'
        echo ') -> dict:'
        echo '    """'
        echo "    $DESCRIPTION"
        echo '    """'
        echo '    return {'
        echo '        "string": string,'
        echo '        "string_optional": string_optional,'
        echo '        "boolean": boolean,'
        echo '        "integer": integer,'
        echo '        "array": array or []'
        echo '    }'
    } > "$output"
}

# Initialize variables
DESCRIPTION=""
FORCE=false
NAME=""
PARAMS=()

# Parse and run
parse_args "$@"
main
