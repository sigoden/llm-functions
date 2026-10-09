#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Execute the sql code.
# @option --code! The code to execute.

# @meta require-tools usql

# @env USQL_DSN! The database connection url. e.g. pgsql://user:pass@host:port
# @env LLM_OUTPUT=/dev/fd/1 The output path

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

main() {
    usql_args=()
    if [[ "$LLM_OUTPUT_COLOR" == "1" ]]; then
        usql_args+=("--color=always")
    fi

    if ! grep -qi '^select' <<<"$argc_code"; then
        "$ROOT_DIR/utils/guard_operation.sh"
    fi
    usql "${usql_args[@]}" -c "$argc_code" "$USQL_DSN" >&1
}

eval "$(argc --argc-eval "$0" "$@")"
