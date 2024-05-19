#!/usr/bin/env bash
set -e

# @describe Executes a shell command.
# @option --command~ Command to execute, such as `ls -la`

main() {
    eval "$argc_command"
}

eval "$(argc --argc-eval "$0" "$@")"