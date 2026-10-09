#!/data/data/com.termux/files/usr/bin/env bash
# @describe Find large files in a directory.
# @option --dir=. The directory to search in.
# @option --min-size=50M The minimum size of files to find.
main() {
    ls_args=("-lh")
    if [[ "$LLM_OUTPUT_COLOR" == "1" ]]; then
        ls_args+=("--color=always")
    fi
    find "$argc_dir" -type f -size +"$argc_min_size" -exec ls "${ls_args[@]}" {} + | sort -hr -k5
}
eval "$(argc --argc-eval "$0" "$@")"
