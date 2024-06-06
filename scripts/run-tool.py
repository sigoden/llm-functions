#!/usr/bin/env python

import os
import json
import sys
import importlib.util

def parse_argv():
    func_name = sys.argv[0]
    func_data = None

    if func_name.endswith("run-tool.py"):
        func_name = sys.argv[1] if len(sys.argv) > 1 else None
        func_data = sys.argv[2] if len(sys.argv) > 2 else None
    else:
        func_name = os.path.basename(func_name)
        func_data = sys.argv[1] if len(sys.argv) > 1 else None

    if func_name.endswith(".py"):
        func_name = func_name[:-3]

    return func_name, func_data

def load_func(func_name):
    func_file_name = f"{func_name}.py"
    func_path = os.path.join(os.environ["LLM_FUNCTIONS_DIR"], f"tools/{func_file_name}")
    if os.path.exists(func_path):
        spec = importlib.util.spec_from_file_location(f"{func_file_name}", func_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    else:
        print(f"Invalid function: {func_file_name}")
        sys.exit(1)

def load_env(file_path):
    try:
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or line == '':
                    continue

                key, *value = line.split('=')
                os.environ[key.strip()] = '='.join(value).strip()
    except FileNotFoundError:
        pass

os.environ["LLM_FUNCTIONS_DIR"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

load_env(os.path.join(os.environ["LLM_FUNCTIONS_DIR"], ".env"))

func_name, func_data = parse_argv()

os.environ["LLM_FUNCTION_NAME"] = func_name

if os.getenv("LLM_FUNCTION_ACTION") == "declarate":
    module = load_func(func_name)
    print(json.dumps(module.declarate(), indent=2))
else:
    if not func_data:
        print("No json data")
        sys.exit(1)

    args = None
    try:
        args = json.loads(func_data)
    except (json.JSONDecodeError, TypeError):
        print("Invalid json data")
        sys.exit(1)

    module = load_func(func_name)
    module.execute(args)