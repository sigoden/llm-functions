#!/data/data/com.termux/files/usr/bin/env bash
set -e

# validate_tools.sh - Shell version of tools validation script

ROOT_DIR="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"
TOOLS_DIR="$ROOT_DIR/tools"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

check_tool() {
    local tool_path="$1"
    local filename=$(basename "$tool_path")
    local errors=()
    
    # Check for description
    local has_desc=0
    if grep -q "@describe" "$tool_path"; then
        has_desc=1
    elif [[ "$filename" == *.py ]] && (grep -q '"""' "$tool_path" || grep -q "'''" "$tool_path"); then
        has_desc=1
    elif [[ "$filename" == *.js ]] && grep -q "/\*\*" "$tool_path"; then
        has_desc=1
    fi

    if [[ $has_desc -eq 0 ]]; then
        errors+=("Missing description (needs @describe, docstring, or JSDoc)")
    fi
    
    # Check for main/run function
    if [[ "$filename" == *.sh ]]; then
        if ! grep -qE "(main\s*\(\)|function main)" "$tool_path"; then
            errors+=("Missing main() function")
        fi
        if ! bash -n "$tool_path" 2>/dev/null; then
            errors+=("Bash syntax error")
        fi
    elif [[ "$filename" == *.py ]]; then
        if ! grep -qE "def (run|main)\(" "$tool_path"; then
            errors+=("Missing run() or main() function")
        fi
        if ! python3 -m py_compile "$tool_path" 2>/dev/null; then
            errors+=("Python syntax error")
        fi
    elif [[ "$filename" == *.js ]]; then
        if ! grep -qE "(exports\.run\s*=|function main\()" "$tool_path"; then
            errors+=("Missing exports.run or main() function")
        fi
        if ! node --check "$tool_path" 2>/dev/null; then
            errors+=("Node.js syntax error")
        fi
    fi
    
    if [[ ${#errors[@]} -eq 0 ]]; then
        echo -e "${GREEN}✓${NC} $filename"
        return 0
    else
        echo -e "${RED}✗${NC} $filename"
        for err in "${errors[@]}"; do
            echo -e "  - $err"
        done
        return 1
    fi
}

main() {
    local total=0
    local valid=0
    
    echo "Validating tools in $TOOLS_DIR..."
    
    for tool in "$TOOLS_DIR"/*.{sh,py,js}; do
        [[ -f "$tool" ]] || continue
        total=$((total + 1))
        if check_tool "$tool"; then
            valid=$((valid + 1))
        fi
    done
    
    echo "--------------------------------"
    echo "Summary: $valid/$total valid tools"
    
    if [[ $valid -lt $total ]]; then
        exit 1
    fi
}

main "$@"
