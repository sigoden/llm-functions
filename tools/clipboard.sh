#!/data/data/com.termux/files/usr/bin/env bash
# @describe Get or set system clipboard.
# @flag --get Get clipboard contents
# @option --set <TEXT> Set clipboard to this text

main() {
    if [[ -n "$argc_get" ]]; then
        termux-clipboard-get
    elif [[ -n "$argc_set" ]]; then
        echo "$argc_set" | termux-clipboard-set
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
