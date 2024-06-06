#!/usr/bin/env python

import os
import json
import io
import sys
import importlib.util

def parse_argv():
    func_file = sys.argv[0]
    func_data = None

    if func_file.endswith("bin.py"):
        func_file = sys.argv[1] if len(sys.argv) > 1 else None
        func_data = sys.argv[2] if len(sys.argv) > 2 else None
    else:
        func_file = os.path.basename(func_file)
        func_data = sys.argv[1] if len(sys.argv) > 1 else None

    if not func_file.endswith(".py"):
        func_file += ".py"

    return func_file, func_data

def load_func(func_file):
    func_path = os.path.join(os.environ["LLM_FUNCTIONS_DIR"], f"tools/{func_file}")
    if os.path.exists(func_path):
        spec = importlib.util.spec_from_file_location(func_file, func_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    else:
        print(f"Invalid function: {func_file}")
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

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

os.environ["LLM_FUNCTIONS_DIR"] = os.path.join(os.path.dirname(__file__), "..")

load_env(os.path.join(os.environ["LLM_FUNCTIONS_DIR"], ".env"))

func_file, func_data = parse_argv()

if os.getenv("LLM_FUNCTION_ACTION") == "declarate":
    module = load_func(func_file)
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

    module = load_func(func_file)
    module.execute(args)