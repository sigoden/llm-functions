#!/usr/bin/env bash
set -e

# @describe Execute the sql code.
# @option --code! The code to execute.

# @meta require-tools usql

# @env USQL_DSN! The database url, e.g. pgsql://user:pass@host/dbname
# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    if ! grep -qi '^select' <<<"$argc_code"; then
        if [ -t 1 ]; then
            read -r -p "Are you sure you want to continue? [Y/n] " ans
            if [[ "$ans" == "N" || "$ans" == "n" ]]; then
                echo "Aborted!"
                exit 1
            fi
        fi
    fi
    usql -c "$argc_code" "$USQL_DSN" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
