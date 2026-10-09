#!/data/data/com.termux/files/usr/bin/env bash
# @describe Get memory usage information.
main() {
    free_args=("-h")
    if [[ "$LLM_OUTPUT_COLOR" == "1" ]]; then
        free_args+=("--color=auto")
    fi
    free "${free_args[@]}"
}
eval "$(argc --argc-eval "$0" "$@")"
