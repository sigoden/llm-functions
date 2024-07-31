#!/usr/bin/env bash
set -e

# @cmd Add a new todo item
# @option --desc! The todo description
add_todo() {
    todos_file="$(_get_todos_file)"
    if [[ -f "$todos_file" ]]; then
        data="$(cat "$todos_file")"
        num="$(echo "$data" | jq '[.[].id] | max + 1')"
    else
        num=1
        data="[]"
    fi
    echo "$data" | \
    jq --arg new_id $num --arg new_desc "$argc_desc" \
        '. += [{"id": $new_id | tonumber, "desc": $new_desc, "done": false}]' \
        > "$todos_file"
    echo "Successfully added todo id=$num" >> "$LLM_OUTPUT"
}

# @cmd Delete an todo item
# @option --id! <INT> The todo id
del_todo() {
    todos_file="$(_get_todos_file)"
    if [[ -f "$todos_file" ]]; then
        data="$(cat "$todos_file")"
        echo "$data" | \
        jq '[.[] | select(.id != '$argc_id')]' \
        > "$todos_file"
        echo "Successfully deleted todo id=$argc_id" >> "$LLM_OUTPUT"
    else
        echo "The operation failed because the todo list is currently empty." >> "$LLM_OUTPUT"
    fi
}

# @cmd Set a todo item status as done
# @option --id! <INT> The todo id
done_todo() {
    todos_file="$(_get_todos_file)"
    if [[ -f "$todos_file" ]]; then
        data="$(cat "$todos_file")"
        echo "$data" | \
        jq '. |= map(if .id == '$argc_id' then .done = true else . end)' \
        > "$todos_file"
        echo "Successfully mark todo id=$argc_id as done" >> "$LLM_OUTPUT"
    else
        echo "The operation failed because the todo list is currently empty." >> "$LLM_OUTPUT"
    fi
}

# @cmd Display the current todo list in json format
list_todos() {
    todos_file="$(_get_todos_file)"
    if [[ -f "$todos_file" ]]; then
        cat "$todos_file" >> "$LLM_OUTPUT"
    else
        echo '[]' >> "$LLM_OUTPUT"
    fi
}

# @cmd Clean the entire todo list
clear_todos() {
    todos_file="$(_get_todos_file)"
    if [[ -f "$todos_file" ]]; then
        if [ -t 1 ]; then
            read -r -p "Clean the entire todo list? [Y/n] " ans
            if [[ "$ans" == "N" || "$ans" == "n" ]]; then
                echo "Aborted!"
                exit 1
            fi
        fi
        rm -rf "$todos_file"
        echo "Successfully cleaned the entire todo list" >> "$LLM_OUTPUT"
    else
        echo "The operation failed because the todo list is currently empty." >> "$LLM_OUTPUT"
    fi
}

_get_todos_file() {
    todos_dir="${LLM_AGENT_CACHE_DIR:-.}"
    mkdir -p "$todos_dir"
    echo "$todos_dir/todos.json"
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
