#!/usr/bin/env bash
set -e

# @cmd Add a new todo item
# @option --desc! The task description
add_todo() {
    todos_file="$(_get_todos_file)"
    if [[ -f "$todos_file" ]]; then
        num="$(jq '[.[].id] | max + 1' "$todos_file")"
        data="$(cat "$todos_file")"
    else
        num=1
        data="[]"
    fi
    echo "$data" | \
    jq --arg new_id $num \
        --arg new_desc "$argc_desc" \
        '. += [{"id": $new_id | tonumber, "desc": $new_desc}]' \
        > "$todos_file"
    echo "Successfully added todo id=$num"
}

# @cmd Delete an existing todo item
# @option --id! <INT> The task id
del_todo() {
    todos_file="$(_get_todos_file)"
    if [[ -f "$todos_file" ]]; then
        data="$(cat "$todos_file")"
        echo "$data" | \
        jq --arg id $argc_id '[.[] | select(.id != ($id | tonumber))]' \
        > "$todos_file"
        echo "Successfully deleted todo id=$argc_id"
    else
        _die "Empty todo list"
    fi
}

# @cmd Display the current todo list in json format.
list_todos() {
    todos_file="$(_get_todos_file)"
    if [[ -f "$todos_file" ]]; then
        cat "$todos_file" 
    else
        _die "Empty todo list"
    fi
}

# @cmd Delete the entire todo list.
clear_todos() {
    todos_file="$(_get_todos_file)"
    if [[ -f "$todos_file" ]]; then
        rm -rf "$todos_file" 
    fi
    echo "Successfully deleted entry todo list"
}

_argc_before() {
    todos_file="$(_get_todos_file)"
    mkdir -p "$(dirname "$todos_file")"
}

_get_todos_file() {
    echo "${LLM_BOT_CACHE_DIR:-/tmp}/todos.json"
}

_die() {
    echo "$*" >&2
    exit 1
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
