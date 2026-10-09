#!/data/data/com.termux/files/usr/bin/env bash
# @describe Get disk space usage.
main() {
    df_args=("-h")
    if [[ "$LLM_OUTPUT_COLOR" == "1" ]]; then
        # Use --color=auto to enable color output if the terminal supports it
        df_args+=("--color=auto")
    fi
    df "${df_args[@]}"
}
eval "$(argc --argc-eval "$0" "$@")"
