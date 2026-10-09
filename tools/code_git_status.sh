#!/data/data/com.termux/files/usr/bin/env bash
# @describe Get a summary of the git status in the current directory.
main() {
    git_args=()
    if [[ "$LLM_OUTPUT_COLOR" == "1" ]]; then
        git_args+=("-c" "color.ui=always")
    fi

    if git rev-parse --is-inside-work-tree > /dev/null 2 >&1; then
        # Use 'git status' for potentially better colorization when available, otherwise fallback to '-s'
        if git "${git_args[@]}" status --help | grep -q -- '--branch'; then
            git "${git_args[@]}" status
        else
            git "${git_args[@]}" status -s
        fi
        echo "--- Branch ---"
        # 'git branch --show-current' might also benefit from color, though less common
        git branch --show-current
    else
        echo "Not a git repository."
    fi
}
main

