#!/usr/bin/env bash
set -e

# @describe Convert documents between formats using pandoc.
# @option --from! <FORMAT>   Input format (e.g. markdown, html, docx, pdf)
# @option --to! <FORMAT>     Output format (e.g. markdown, html, docx, pdf)
# @option --input <FILE>     Input file path (optional, if omitted use --contents)
# @option --output <FILE>    Output file path (optional, default: stdout)
# @option --contents <TEXT>  Raw input text instead of file

# @env LLM_OUTPUT=/dev/stdout The output path

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

main() {
  "$ROOT_DIR/utils/guard_operation.sh"
  input_arg=()
  output_arg=()

  if [[ -n "$argc_input" ]]; then
    input_arg+=("$argc_input")
  elif [[ -n "$argc_contents" ]]; then
    input_arg+=(-)
  else
    echo "Error: Either --input or --contents must be provided." >&2
    exit 1
  fi

  if [[ -n "$argc_output" ]]; then
    output_arg+=(-o "$argc_output")
  else
    output_arg+=(-)
  fi

  if [[ -n "$argc_contents" ]]; then
    printf "%s" "$argc_contents" | pandoc -f "$argc_from" -t "$argc_to" "${output_arg[@]}" >>"$LLM_OUTPUT"
  else
    pandoc -f "$argc_from" -t "$argc_to" "${input_arg[@]}" "${output_arg[@]}" >>"$LLM_OUTPUT"
  fi
}

eval "$(argc --argc-eval "$0" "$@")"
