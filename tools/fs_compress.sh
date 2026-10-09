#!/data/data/com.termux/files/usr/bin/env bash
# @describe Compress a file or folder into a tar.gz archive.
# @arg path! The path to compress.
# @option --output! The output filename (e.g., archive.tar.gz).
main() {
    tar -czvf "$argc_output" "$argc_path"
}
eval "$(argc --argc-eval "$0" "$@")"
