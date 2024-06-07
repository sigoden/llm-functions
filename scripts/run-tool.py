#!/usr/bin/env python

import os
import json
import sys
import importlib.util


def parse_argv():
    tool_name = sys.argv[0]
    tool_data = None

    if tool_name.endswith("run-tool.py"):
        tool_name = sys.argv[1] if len(sys.argv) > 1 else None
        tool_data = sys.argv[2] if len(sys.argv) > 2 else None
    else:
        tool_name = os.path.basename(tool_name)
        tool_data = sys.argv[1] if len(sys.argv) > 1 else None

    if tool_name.endswith(".py"):
        tool_name = tool_name[:-3]

    return tool_name, tool_data


def load_module(tool_name):
    tool_file_name = f"{tool_name}.py"
    tool_path = os.path.join(os.environ["LLM_ROOT_DIR"], f"tools/{tool_file_name}")
    if os.path.exists(tool_path):
        spec = importlib.util.spec_from_file_location(f"{tool_file_name}", tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    else:
        print(f"Invalid function: {tool_file_name}")
        sys.exit(1)


def load_env(file_path):
    try:
        with open(file_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or line == "":
                    continue

                key, *value = line.split("=")
                os.environ[key.strip()] = "=".join(value).strip()
    except FileNotFoundError:
        pass


LLM_ROOT_DIR = os.environ["LLM_ROOT_DIR"] = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

load_env(os.path.join(LLM_ROOT_DIR, ".env"))

tool_name, tool_data = parse_argv()

os.environ["LLM_TOOL_NAME"] = tool_name
os.environ["LLM_TOOL_CACHE_DIR"] = os.path.join(LLM_ROOT_DIR, "cache", tool_name)

if not tool_data:
    print("No json data")
    sys.exit(1)

data = None
try:
    data = json.loads(tool_data)
except (json.JSONDecodeError, TypeError):
    print("Invalid json data")
    sys.exit(1)

module = load_module(tool_name)
module.run(**data)
