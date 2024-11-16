#!/usr/bin/env bash

# Guard an operation with a confirmation prompt.

main() {
    if [ -t 1 ]; then
        confirmation_prompt="${1:-"Are you sure you want to continue?"}"
        read -r -p "$confirmation_prompt [Y/n] " ans
        if [[ "$ans" == "N" || "$ans" == "n" ]]; then
            echo "error: aborted!" 2>&1
            exit 1
        fi
    fi
}

main "$@"
