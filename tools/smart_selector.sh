#!/data/data/com.termux/files/usr/bin/env bash
set -eo pipefail

# @describe AI-powered tool selection based on user intent
# @option --query! User query or task description
# @option --context Additional context for selection

cleanup() {
    local exit_code=$?
    exit $exit_code
}

trap cleanup EXIT

main() {
    local query="${argc_query,,}" # lowercase
    local context="${argc_context:-}"
    
    # Analyze query and suggest relevant tools
    # Note: We need to use the full path to argc if not in PATH
    local ARGC_BIN="${ARGC_BIN:-argc}"
    
    # Get list of tools from tools.txt or directory
    local tools_list=""
    if [[ -f "tools.txt" ]]; then
        tools_list=$(grep -v '^#' tools.txt | awk '{print $1}')
    else
        tools_list=$(ls tools/ | sed 's|\..*$||' | sort -u)
    fi
    
    local suggestions=""
    
    for tool in $tools_list; do
        # Try to get description
        local description=""
        if [[ -f "tools/${tool}.sh" ]]; then
            description=$(grep "@describe" "tools/${tool}.sh" | head -n1 | sed 's|.*@describe ||')
        elif [[ -f "tools/${tool}.js" ]]; then
             description=$(grep "@description" "tools/${tool}.js" | head -n1 | sed 's|.*@description ||')
        elif [[ -f "tools/${tool}.py" ]]; then
             description=$(grep "@describe" "tools/${tool}.py" | head -n1 | sed 's|.*@describe ||')
        fi
        
        description="${description,,}"
        
        if [[ "$description" =~ $query ]] || [[ "${tool,,}" =~ $query ]]; then
            suggestions="${suggestions}${tool} "
        fi
    done
    
    if [[ -z "$suggestions" ]]; then
        echo "No specific tool suggestions found for: $argc_query" >> "${LLM_OUTPUT:-/dev/stdout}"
    else
        echo "Suggested tools: ${suggestions% }" >> "${LLM_OUTPUT:-/dev/stdout}"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
