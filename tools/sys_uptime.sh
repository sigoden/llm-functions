#!/data/data/com.termux/files/usr/bin/env bash
# @describe Get system uptime and load average.
main() {
    # The 'uptime' command does not have a direct color flag.
    uptime
}
eval "$(argc --argc-eval "$0" "$@")"
