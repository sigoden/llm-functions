#!/usr/bin/env python

# Usage: ./run-agent.py <agent-name> <agent-func> <agent-data>

import os
import re
import json
import sys
import importlib.util


def main():
    (agent_name, agent_func, raw_data) = parse_argv("run-agent.py")
    agent_data = parse_raw_data(raw_data)

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    setup_env(root_dir, agent_name, agent_func)

    agent_tools_path = os.path.join(root_dir, f"agents/{agent_name}/tools.py")
    run(agent_name, agent_tools_path, agent_func, agent_data)


def parse_raw_data(data):
    if not data:
        raise ValueError("No JSON data")

    try:
        return json.loads(data)
    except Exception:
        raise ValueError("Invalid JSON data")


def parse_argv(this_file_name):
    argv = sys.argv[:] + [None] * max(0, 4 - len(sys.argv))

    agent_name = argv[0]
    agent_func = ""
    agent_data = ""

    if agent_name.endswith(this_file_name):
        if len(sys.argv) > 3:
            agent_name = sys.argv[1]
            agent_func = sys.argv[2]
            agent_data = sys.argv[3]
    else:
        if len(sys.argv) > 2:
            agent_name = os.path.basename(agent_name)
            agent_func = sys.argv[1]
            agent_data = sys.argv[2]

    if agent_name and agent_name.endswith(".py"):
        agent_name = agent_name[:-3]

    if (not agent_data) or (not agent_func) or (not agent_name):
        print("Usage: ./run-agent.py <agent-name> <agent-func> <agent-data>", file=sys.stderr)
        sys.exit(1)   

    return agent_name, agent_func, agent_data


def setup_env(root_dir, agent_name, agent_func):
    load_env(os.path.join(root_dir, ".env"))
    os.environ["LLM_ROOT_DIR"] = root_dir
    os.environ["LLM_AGENT_NAME"] = agent_name
    os.environ["LLM_AGENT_FUNC"] = agent_func
    os.environ["LLM_AGENT_ROOT_DIR"] = os.path.join(root_dir, "agents", agent_name)
    os.environ["LLM_AGENT_CACHE_DIR"] = os.path.join(root_dir, "cache", agent_name)


def load_env(file_path):
    try:
        with open(file_path, "r") as f:
            lines = f.readlines()
    except:
        return

    env_vars = {}

    for line in lines:
        line = line.strip()
        if line.startswith("#") or not line:
            continue

        key, *value_parts = line.split("=")
        env_name = key.strip()

        if env_name not in os.environ:
            env_value = "=".join(value_parts).strip()
            if (env_value.startswith('"') and env_value.endswith('"')) or (env_value.startswith("'") and env_value.endswith("'")):
                env_value = env_value[1:-1]
            env_vars[env_name] = env_value

    os.environ.update(env_vars)


def run(agent_name, agent_path, agent_func, agent_data):
    try:
        spec = importlib.util.spec_from_file_location(
            os.path.basename(agent_path), agent_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except:
        raise Exception(f"Unable to load agent tools at '{agent_path}'")

    if not hasattr(mod, agent_func):
        raise Exception(f"Not module function '{agent_func}' at '{agent_path}'")

    value = getattr(mod, agent_func)(**agent_data)
    return_to_llm(value)
    dump_result(rf'{agent_name}:{agent_func}')


def return_to_llm(value):
    if value is None:
        return

    if "LLM_OUTPUT" in os.environ:
        writer = open(os.environ["LLM_OUTPUT"], "w")
    else:
        writer = sys.stdout

    value_type = type(value).__name__
    if value_type in ("str", "int", "float", "bool"):
        writer.write(str(value))
    elif value_type == "dict" or value_type == "list":
        value_str = json.dumps(value, indent=2)
        assert value == json.loads(value_str)
        writer.write(value_str)


def dump_result(name):
    if (not os.getenv("LLM_DUMP_RESULTS")) or (not os.getenv("LLM_OUTPUT")) or (not os.isatty(1)):
        return

    show_result = False
    try:
        if re.search(rf'\b({os.environ["LLM_DUMP_RESULTS"]})\b', name):
            show_result = True
    except:
        pass

    if not show_result:
        return

    try:
        with open(os.environ["LLM_OUTPUT"], "r", encoding="utf-8") as f:
            data = f.read()
    except:
        return

    print(f"\x1b[2m----------------------\n{data}\n----------------------\x1b[0m")


if __name__ == "__main__":
    main()