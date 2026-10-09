#!/data/data/com.termux/files/usr/bin/env bash
# @describe List installed packages (pkg).
main() {
    pkg list-installed
}
eval "$(argc --argc-eval "$0" "$@")"
