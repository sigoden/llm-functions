#!/data/data/com.termux/files/usr/bin/env bash
# @describe Extract a tar.gz or zip archive.
# @arg archive! The archive file to extract.
# @option --dir=. The directory to extract into.
main() {
    if [[ "$argc_archive" == *.zip ]]; then
        unzip "$argc_archive" -d "$argc_dir"
    else
        tar -xzvf "$argc_archive" -C "$argc_dir"
    fi
}
eval "$(argc --argc-eval "$0" "$@")"
