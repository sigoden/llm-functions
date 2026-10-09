#!/data/data/com.termux/files/usr/bin/env bash
# @describe Calculate SHA256 checksum of a file.
# @arg file! The file to check.
main() {
    sha256sum "$argc_file"
}
eval "$(argc --argc-eval "$0" "$@")"
