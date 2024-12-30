#!/usr/bin/env bash

set -e

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# @meta require-tools usql
# @env LLM_AGENT_VAR_DSN! The database connection url. e.g. pgsql://user:pass@host:port 

# @cmd Execute a SELECT query
# @option --query!                  SELECT SQL query to execute
read_query() {
    if ! grep -qi '^select' <<<"$argc_query"; then
        echo "error: only SELECT query is allowed" >&2
        exit 1
    fi
    _run_sql "$argc_query"
}

# @cmd Execute an SQL query
# @option --query!                  SQL query to execute
write_query() {
    "$ROOT_DIR/utils/guard_operation.sh" "Execute SQL?"
    _run_sql "$argc_query"
}

# @cmd List all tables
list_tables() {
    _run_sql "\dt+"
}

# @cmd Get the schema information for a specific table
# @option --table-name!             Name of the table to describe
describe_table() {
    _run_sql "\d $argc_table_name"
}

_run_sql() {
    usql "$LLM_AGENT_VAR_DSN" -c "$1" >> "$LLM_OUTPUT"
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
