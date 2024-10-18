#!/usr/bin/env python

import os
import json
import sys
import importlib.util


def main():
    (agent_name, agent_func, raw_data) = parse_argv("run-agent.py")
    agent_data = parse_raw_data(raw_data)

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    setup_env(root_dir, agent_name, agent_func)

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
            if env_value.startswith('"') and env_value.endswith('"'):
                env_value = env_value[1:-1]
            elif env_value.startswith("'") and env_value.endswith("'"):
                env_value = env_value[1:-1]
            env_vars[env_name] = env_value

    os.environ.update(env_vars)


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
    dump_result()


def dump_result():
    if not os.isatty(1):
        return

    if not os.getenv("LLM_OUTPUT"):
        return

    show_result = False
    agent_name = os.environ["LLM_AGENT_NAME"].upper().replace("-", "_")
    agent_env_name = f"LLM_AGENT_DUMP_RESULT_{agent_name}"
    agent_env_value = os.getenv(agent_env_name, os.getenv("LLM_AGENT_DUMP_RESULT"))

    func_name = os.environ["LLM_AGENT_FUNC"].upper().replace("-", "_")
    func_env_name = f"{agent_env_name}_{func_name}"
    func_env_value = os.getenv(func_env_name)

    if agent_env_value in ("1", "true"):
        if func_env_value not in ("0", "false"):
            show_result = True
    else:
        if func_env_value in ("1", "true"):
            show_result = True

    if not show_result:
        return

    try:
        with open(os.environ["LLM_OUTPUT"], "r", encoding="utf-8") as f:
            data = f.read()
    except:
        return

    print(f"\x1b[2m----------------------\n{data}\n----------------------\x1b[0m")


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
    try:
        main()
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)
