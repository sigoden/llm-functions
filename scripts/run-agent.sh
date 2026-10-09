#!/usr/bin/env bash
set -e

agent_name="$1"
agent_func="$2"
agent_data="$3"

root_dir="$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd)"

# Parse JSON data to command line arguments
jq_script="$(cat <<-'EOF'
def escape_shell_word:
  tostring
  | gsub("'"; "'\"'\"'")
  | gsub("\n"; "'$'\\n''")
  | "'\(.)'";
def to_args:
    to_entries | .[] | 
    (.key | split("_") | join("-")) as $key |
    if .value | type == "array" then
        .value | .[] | "--\($key) \(. | escape_shell_word)"
    elif .value | type == "boolean" then
        if .value then "--\($key)" else "" end
    else
        "--\($key) \(.value | escape_shell_word)"
    end;
[ to_args ] | join(" ")
EOF
)"

args=""
if [[ -n "$agent_data" && "$agent_data" != "{}" ]]; then
    args="$(echo "$agent_data" | jq -r "$jq_script" 2>/dev/null)" || {
        echo "error: invalid JSON data" >&2
        exit 1
    }
fi

export LLM_ROOT_DIR="$root_dir"
export LLM_AGENT_NAME="$agent_name"
export LLM_AGENT_ROOT_DIR="$root_dir/agents/$agent_name"
export LLM_AGENT_CACHE_DIR="$root_dir/cache/$agent_name"
export LLM_OUTPUT="${LLM_OUTPUT:-/dev/stdout}"

eval "\"$root_dir/agents/$agent_name/tools.sh\" \"$agent_func\" $args"
