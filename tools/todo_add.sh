#!/data/data/com.termux/files/usr/bin/env bash
# @describe Add a task to a local todo.txt file.
# @arg task! The task description.
main() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $argc_task" >> ~/todo.txt
    echo "Task added to ~/todo.txt"
}
eval "$(argc --argc-eval "$0" "$@")"
