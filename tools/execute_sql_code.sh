#!/usr/bin/env bash
set -e

# @describe Execute the sql code.
# @option --code! The code to execute.

# @meta require-tools usql

# @env USQL_DSN! The database connection url. e.g. pgsql://user:pass@host:port
# @env LLM_OUTPUT=/dev/stdout The output path

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

main() {
    if ! grep -qi '^select' <<<"$argc_code"; then
        "$ROOT_DIR/utils/guard_operation.sh"
    fi
    usql -c "$argc_code" "$USQL_DSN" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
