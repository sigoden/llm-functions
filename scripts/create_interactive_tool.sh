#!/data/data/com.termux/files/usr/bin/env bash
# ==============================================================================
# create_interactive_tool.sh — Interactive Tool Boilerplate Generator
# 
# Generates boilerplate code for new tool scripts in bash, JavaScript, or Python.
# Interactive mode: prompts for all inputs instead of CLI arguments.
# ==============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Supported extensions
SUPPORTED_EXTS=('sh' 'js' 'py')

# Usage
usage() {
    echo -e "${BLUE}Usage:${NC} $0 [options]"
    echo -e "  ${BLUE}--non-interactive${NC}   Run in non-interactive mode (CLI args)"
    echo -e "  ${BLUE}--force${NC}           Override existing file"
    echo -e "  ${BLUE}--description <text>${NC}  Tool description"
    echo -e "  ${BLUE}--help${NC}              Show this help"
    exit 0
}

# Error handler
die() {
    echo -e "${RED}Error:${NC} $*" >&2
    exit 1
}

# Prompt for input with default
prompt() {
    local prompt_text="$1"
    local default="$2"
    local result
    
    if [[ -n "$default" ]]; then
        read -rp "$(echo -e \"${CYAN}${prompt_text}${NC} [${GREEN}${default}${NC}]: \")" result
        echo "${result:-$default}"
    else
        read -rp "$(echo -e \"${CYAN}${prompt_text}${NC}: \")" result
        echo "$result"
    fi
}

# Main function
main() {
    # Get tool name
    NAME=$(prompt "Tool name" "")
    [[ -z "$NAME" ]] && die "Tool name is required"
    
    # Validate extension
    ext="${NAME##*.}"
    if [[ "$ext" == "$NAME" ]]; then
        die "No extension. Supported: ${SUPPORTED_EXTS[*]}"
    fi
    if [[ ! " ${SUPPORTED_EXTS[*]} " =~ " $ext " ]]; then
        die "Invalid extension: $ext. Supported: ${SUPPORTED_EXTS[*]}"
    fi
    
    # Get description
    DESCRIPTION=$(prompt "Tool description" "A useful tool")
    
    # Get parameters
    echo -e "\n${YELLOW}Parameters (press Enter to skip):${NC}"
    PARAMS=()
    while true; do
        param=$(prompt "  Parameter name (or done)" "")
        [[ -z "$param" ]] && break
        echo -e "  ${CYAN}Type:${NC}"
        echo -e "    1) Required string"
        echo -e "    2) Optional string"
        echo -e "    3) Required integer"
        echo -e "    4) Optional integer"
        echo -e "    5) Boolean flag"
        echo -e "    6) Array (repeatable)"
        read -rp "  ${CYAN}Choose [1-6]${NC}: " ptype
        case "$ptype" in
            1) PARAMS+=("${param}!");;
            2) PARAMS+=("${param}");;
            3) PARAMS+=("${param}#");;
            4) PARAMS+=("${param}#?");;
            5) PARAMS+=("${param}?");;
            6) PARAMS+=("${param}*");;
            *) PARAMS+=("${param}");;
        esac
    done
    
    # Check force
    FORCE=false
    if [[ -f "tools/$NAME" ]]; then
        echo -e "\n${YELLOW}File exists. Overwrite?${NC}"
        if confirm "Overwrite?" "n"; then
            FORCE=true
        fi
    fi
    
    # Summary
    echo -e "\n${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Summary${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo "  Name:        $NAME"
    echo "  Description: $DESCRIPTION"
    echo "  Parameters:  ${PARAMS[*]:-none}"
    echo "  Force:       $FORCE"
    echo -e "\n${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    
    if confirm "Create tool?" "y"; then
        local output="tools/$NAME"
        local ext="${NAME##*.}"
        
        # Ensure tools directory exists
        mkdir -p "$(dirname "$output")"
        
        # Generate based on extension
        case "$ext" in
            sh) create_sh "$output" ;;
            js) create_js "$output" ;;
            py) create_py "$output" ;;
        esac
        
        chmod +x "$output"
        echo -e "${GREEN}Created:${NC} $output"
    else
        echo -e "${YELLOW}Cancelled.${NC}"
        exit 0
    fi
}

# Create Bash tool
create_sh() {
    local output="$1"
    cat > "$output" << 'EOF'
#!/usr/bin/env bash
# ==============================================================================
# $NAME — Bash Tool
# ==============================================================================

set -euo pipefail

main() {
    echo "Hello from $NAME!"
}

# Entry point
eval "$(argc --argc-eval \"$0\" \"$@\")"
EOF
}

# Create JavaScript tool
create_js() {
    local output="$1"
    cat > "$output" << 'EOF'
/**
 * $DESCRIPTION
 */
exports.run = function(args) {
    return {
        message: "Hello from $NAME!"
    };
};
EOF
}

# Create Python tool
create_py() {
    local output="$1"
    cat > "$output" << 'EOF'
#!/usr/bin/env python3
# ==============================================================================
# $NAME — Python Tool
# ==============================================================================

def run():
    """$DESCRIPTION"""
    return {
        "message": "Hello from $NAME!"
    }

if __name__ == "__main__":
    print("Hello from $NAME!")
EOF
}

# Confirm function
confirm() {
    local prompt_text="$1"
    local default="${2:-n}"
    local response
    
    while true; do
        if [[ "$default" == "y" ]]; then
            read -rp "$(echo -e \"${CYAN}${prompt_text}${NC} [Y/n]: \")\" response
        else
            read -rp "$(echo -e \"${CYAN}${prompt_text}${NC} [y/N]: \")\" response
        fi
        response="${response:-$default}"
        case "$response" in
            y|Y) return 0 ;;
            n|N) return 1 ;;
            *) echo "Please answer y or n" ;;
        esac
    done
}

# Parse arguments
DESCRIPTION=""
FORCE=false
NAME=""
PARAMS=()

# Parse arguments
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

# Run mode selection
if [[ "${1:-}" == "--non-interactive" ]] || [[ "${1:-}" == "-n" ]]; then
    shift
    non_interactive_mode "$@"
else
    interactive_mode
fi
