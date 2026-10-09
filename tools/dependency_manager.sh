#!/data/data/com.termux/files/usr/bin/env bash
set -eo pipefail

# @describe Manage tool dependencies
# @option --action! Action (install|check|list)
# @option --tool Tool name

cleanup() {
    local exit_code=$?
    exit $exit_code
}

trap cleanup EXIT

install_tool_deps() {
    local tool="$1"
    local deps_file="tools/${tool}.deps"
    
    if [[ -f "$deps_file" ]]; then
        echo "Installing dependencies for $tool..."
        while read -r dep || [[ -n "$dep" ]]; do
            [[ -z "$dep" || "$dep" == "#"* ]] && continue
            echo "Processing: $dep"
            if [[ "$dep" == "pip:"* ]]; then
                pip install "${dep#pip:}"
            elif [[ "$dep" == "npm:"* ]]; then
                npm install -g "${dep#npm:}"
            elif [[ "$dep" == "pkg:"* ]]; then
                pkg install "${dep#pkg:}"
            else
                echo "Unknown dependency type: $dep"
            fi
        done < "$deps_file"
    else
        echo "No dependencies file found for $tool"
    fi
}

check_tool_deps() {
    local tool="$1"
    local deps_file="tools/${tool}.deps"
    
    if [[ -f "$deps_file" ]]; then
        echo "Checking dependencies for $tool..."
        while read -r dep || [[ -n "$dep" ]]; do
            [[ -z "$dep" || "$dep" == "#"* ]] && continue
            # Basic check (very simplified)
            echo "Checking: $dep"
        done < "$deps_file"
    else
        echo "No dependencies file found for $tool"
    fi
}

list_all_deps() {
    echo "Listing all tool dependencies:"
    for f in tools/*.deps; do
        [[ -e "$f" ]] || continue
        echo "--- $(basename "$f" .deps) ---"
        cat "$f"
    done
}

main() {
    case "$argc_action" in
        install) 
            if [[ -z "${argc_tool:-}" ]]; then echo "Tool name required for install"; exit 1; fi
            install_tool_deps "$argc_tool" 
            ;;
        check) 
            if [[ -z "${argc_tool:-}" ]]; then echo "Tool name required for check"; exit 1; fi
            check_tool_deps "$argc_tool" 
            ;;
        list) 
            list_all_deps 
            ;;
    esac
}

eval "$(argc --argc-eval "$0" "$@")"
