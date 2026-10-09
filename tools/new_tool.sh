#!/data/data/com.termux/files/usr/bin/env bash
# ==============================================================================
# new_tool.sh — New Tool
#
# @describe Brief description of the tool
# @option --input-file! <PATH>  Required input file path
# @option --output-dir <PATH>   Optional output directory
# @flag --verbose               Boolean flag
# ==============================================================================

set -e

_llm_emit() {
  local content="$1"
  local out="${LLM_OUTPUT:-/dev/stdout}"
  if [[ -z "${LLM_OUTPUT:-}" || "${LLM_OUTPUT}" == "/dev/stdout" ]]; then
    printf '%s\n' "$content"
    return
  fi
  mkdir -p "$(dirname "$out")" 2>/dev/null || true
  printf '%s\n' "$content" >"$out"
}

_log() {
  printf '%s\n' "$*" >&2
}

_die() {
  _log "error: $*"
  exit 1
}

main() {
  echo "Processing input file: ${argc_input_file}"
  echo "Output dir: ${argc_output_dir}"
  local result
  result="{\"status\":\"ok\"}"
  _llm_emit "$result"
}

main
eval "$(argc --argc-eval \"$0\" \"$@\")"
