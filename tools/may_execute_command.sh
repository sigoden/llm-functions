#!/usr/bin/env bash
set -e

# @describe Runs a shell command.
# @option --command! The command to execute.

main() {
    eval "$argc_command"
}

eval "$(argc --argc-eval "$0" "$@")"