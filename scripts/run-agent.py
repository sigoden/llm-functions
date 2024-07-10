#!/usr/bin/env python

import os
import json
import sys
import importlib.util


def main():
    (agent_name, agent_func, raw_data) = parse_argv("run-agent.py")
    agent_data = parse_raw_data(raw_data)

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    setup_env(root_dir, agent_name)

    agent_tools_path = os.path.join(root_dir, f"agents/{agent_name}/tools.py")
    run(agent_tools_path, agent_func, agent_data)


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
    agent_data = None

    if agent_name.endswith(this_file_name):
        agent_name = sys.argv[1]
        agent_func = sys.argv[2]
        agent_data = sys.argv[3]
    else:
        agent_name = os.path.basename(agent_name)
        agent_func = sys.argv[1]
        agent_data = sys.argv[2]

    if agent_name.endswith(".py"):
        agent_name = agent_name[:-3]

    return agent_name, agent_func, agent_data


def setup_env(root_dir, agent_name):
    load_env(os.path.join(root_dir, ".env"))
    os.environ["LLM_ROOT_DIR"] = root_dir
    os.environ["LLM_AGENT_NAME"] = agent_name
    os.environ["LLM_AGENT_ROOT_DIR"] = os.path.join(root_dir, "agents", agent_name)
    os.environ["LLM_AGENT_CACHE_DIR"] = os.path.join(root_dir, "cache", agent_name)


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


def run(agent_path, agent_func, agent_data):
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


def return_to_llm(value):
    if value is None:
        return

    if "LLM_OUTPUT" in os.environ:
        writer = open(os.environ["LLM_OUTPUT"], "w")
    else:
        writer = sys.stdout

    value_type = type(value).__name__
    if value_type in ("str", "int", "float", "bool"):
        writer.write(value)
    elif value_type == "dict" or value_type == "list":
        value_str = json.dumps(value, indent=2)
        assert value == json.loads(value_str)
        writer.write(value_str)


if __name__ == "__main__":
    main()
