#!/usr/bin/env python

import os
import json
import sys
import importlib.util


def main():
    (bot_name, bot_func, raw_data) = parse_argv("run-bot.py")
    bot_data = parse_raw_data(raw_data)

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    setup_env(root_dir, bot_name)

    bot_tools_path = os.path.join(root_dir, f"bots/{bot_name}/tools.py")
    run(bot_tools_path, bot_func, bot_data)


def parse_raw_data(data):
    if not data:
        raise ValueError("No JSON data")

    try:
        return json.loads(data)
    except Exception:
        raise ValueError("Invalid JSON data")


def parse_argv(this_file_name):
    argv = sys.argv[:] + [None] * max(0, 4 - len(sys.argv))

    bot_name = argv[0]
    bot_func = ""
    bot_data = None

    if bot_name.endswith(this_file_name):
        bot_name = sys.argv[1]
        bot_func = sys.argv[2]
        bot_data = sys.argv[3]
    else:
        bot_name = os.path.basename(bot_name)
        bot_func = sys.argv[1]
        bot_data = sys.argv[2]

    if bot_name.endswith(".py"):
        bot_name = bot_name[:-3]

    return bot_name, bot_func, bot_data


def setup_env(root_dir, bot_name):
    os.environ["LLM_ROOT_DIR"] = root_dir
    load_env(os.path.join(root_dir, ".env"))
    os.environ["LLM_BOT_NAME"] = bot_name
    os.environ["LLM_BOT_ROOT_DIR"] = os.path.join(root_dir, "bots", bot_name)
    os.environ["LLM_BOT_CACHE_DIR"] = os.path.join(root_dir, "cache", bot_name)


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


def run(bot_path, bot_func, bot_data):
    try:
        spec = importlib.util.spec_from_file_location(
            os.path.basename(bot_path), bot_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except:
        raise Exception(f"Unable to load bot tools at '{bot_path}'")

    if not hasattr(mod, bot_func):
        raise Exception(f"Not module function '{bot_func}' at '{bot_path}'")

    value = getattr(mod, bot_func)(**bot_data)
    dump_value(value)


def dump_value(value):
    if value is None:
        return

    value_type = type(value).__name__
    if value_type in ("str", "int", "float", "bool"):
        print(value)
    elif value_type == "dict" or value_type == "list":
        value_str = json.dumps(value, indent=2)
        assert value == json.loads(value_str)
        print(value_str)


if __name__ == "__main__":
    main()
