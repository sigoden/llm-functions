import json
import sys
import os
from json import JSONDecodeError


def add_todo(desc: str):
    """Add a new todo item
    Args:
        desc: The task description
    """
    todos_file = _get_todos_file()
    try:
        with open(todos_file, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, JSONDecodeError):
        data = []
    num = max([item["id"] for item in data] + [0]) + 1
    data.append({"id": num, "desc": desc})
    with open(todos_file, "w") as f:
        json.dump(data, f)
    print(f"Successfully added todo id={num}")


def del_todo(id: int):
    """Delete an existing todo item
    Args:
        id: The task id
    """
    todos_file = _get_todos_file()
    try:
        with open(todos_file, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, JSONDecodeError):
        print("Empty todo list")
        return
    data = [item for item in data if item["id"] != id]
    with open(todos_file, "w") as f:
        json.dump(data, f)
    print(f"Successfully deleted todo id={id}")


def list_todos():
    """Display the current todo list in json format."""
    todos_file = _get_todos_file()
    try:
        with open(todos_file, "r") as f:
            print(f.read())
    except FileNotFoundError:
        print("[]")


def clear_todos():
    """Delete the entire todo list."""
    os.remove(_get_todos_file())


def _get_todos_file() -> str:
    cache_dir=os.environ.get("LLM_BOT_CACHE_DIR", "/tmp")
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "todos.json")
