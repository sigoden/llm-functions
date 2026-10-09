#!/data/data/com.termux/files/usr/bin/env bash
# @describe Get CPU information and usage.
main() {
    # lscpu itself does not have a direct --color flag, but respects TERM and LS_COLORS environment variables.
    # The output here is usually plain text by default.
    lscpu | head -n 20
    echo "--- Current Usage ---"
    # top command usually provides colored output if the terminal supports it.
    top -bn1 | grep "CPU" | head -n 5
}
eval "$(argc --argc-eval "$0" "$@")"
