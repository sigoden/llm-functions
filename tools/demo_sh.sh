#!/data/data/com.termux/files/usr/bin/env bash
# ==============================================================================
# demo_sh.sh — Bash Tool Demo
#
# Demonstrates how to create a tool using Bash with various parameter types.
# Supports: required/optional strings, enums, integers, numbers, booleans, arrays.
#
# @describe Demo Bash tool with all parameter types
# @option --string! <VALUE> Define a required string property
# @option --string-enum![foo|bar] Define a required string property with enum
# @option --string-optional <VALUE> Define an optional string property
# @flag --boolean Define a boolean property
# @option --integer! <INT> Define a required integer property
# @option --number! <NUM> Define a required number property
# @option --array+ <VALUE> Define a required string array property
# @option --array-optional* <VALUE> Define an optional string array property
# @env LLM_OUTPUT=/dev/fd/1 Output path

set -euo pipefail

main() {
    echo "string: ${argc_string}"
    echo "string_enum: ${argc_string_enum}"
    echo "string_optional: ${argc_string_optional}"
    echo "boolean: ${argc_boolean}"
    echo "integer: ${argc_integer}"
    echo "number: ${argc_number}"
    echo "array: ${argc_array[*]}"
    echo "array_optional: ${argc_array_optional[*]}"
}

eval "$(argc --argc-eval "$0" "$@")"
