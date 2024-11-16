#!/usr/bin/env bash

main() {
    if [[ "$#" -ne 2 ]]; then
        echo "Usage: guard_path.sh <path> <confirmation_prompt>"
        exit 1
    fi
    path="$(_to_realpath "$1")"
    confirmation_prompt="$2"
    if [[ ! "$path" == "$(pwd)"* ]]; then
        if [ -t 1 ]; then
            read -r -p "$confirmation_prompt [Y/n] " ans
            if [[ "$ans" == "N" || "$ans" == "n" ]]; then
                echo "Aborted!"
                exit 1
            fi
        fi
    fi
}

_to_realpath() {
    path="$1"
    if [[ $OS == "Windows_NT" ]]; then
        path="$(cygpath -u "$path")"
    fi
    awk -v path="$path" -v pwd="$PWD" '
BEGIN {
    if (path !~ /^\//) {
        path = pwd "/" path
    }
    if (path ~ /\/\.{1,2}?$/) {
        isDir = 1
    }
    split(path, parts, "/")
    newPartsLength = 0
    for (i = 1; i <= length(parts); i++) {
        part = parts[i]
        if (part == "..") {
            if (newPartsLength > 0) {
                delete newParts[newPartsLength--]
            }
        } else if (part != "." && part != "") {
            newParts[++newPartsLength] = part
        }
    }
    if (isDir == 1 || newPartsLength == 0) {
        newParts[++newPartsLength] = ""
    }
    printf "/"
    for (i = 1; i <= newPartsLength; i++) {
        newPart = newParts[i]
        printf newPart
        if (i < newPartsLength) {
            printf "/"
        }
    }
}'
}

main "$@"
