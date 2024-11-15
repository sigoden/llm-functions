#!/usr/bin/env bash
set -e

# @describe Apply a patch to a file at the specified path.
# This can be used to edit the file, without having to rewrite the whole file.

# @option --path! The path of the file to apply to
# @option --contents! The patch to apply to the file
#
# Here is an example of a patch block that can be applied to modify the file to request the user's name:
# --- a/hello.py
# +++ b/hello.py
# \@@ ... @@
#  def hello():
# -    print("Hello World")
# +    name = input("What is your name? ")
# +    print(f"Hello {name}")

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    if [ ! -f "$argc_path" ]; then
        echo "Not found file: $argc_path"
        exit 1
    fi
    root_dir="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
    new_contents="$(awk -f "$root_dir/utils/patch.awk" "$argc_path" <(printf "%s" "$argc_contents"))"
    printf "%s" "$new_contents" | git diff --no-index "$argc_path" - || true
    if [ -t 1 ]; then
        echo
        read -r -p "Apply changes? [Y/n] " ans
        if [[ "$ans" == "N" || "$ans" == "n" ]]; then
            echo "Aborted!"
            exit 1
        fi
    fi
    printf "%s" "$new_contents" > "$argc_path"

    echo "The patch applied to: $argc_path" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
