#!/usr/bin/env python

import os
import json
import sys
import importlib.util

def load_module(func_name):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    func_path = os.path.join(base_dir, f"../py/{func_name}")
    if os.path.exists(func_path):
        spec = importlib.util.spec_from_file_location(func_name, func_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    else:
        print(f"Invalid py function: {func_name}")
        sys.exit(1)

func_name = sys.argv[0]
if func_name.endswith("cmd.py"):
    func_name = sys.argv[1]
else:
    func_name = os.path.basename(func_name)

if not func_name.endswith(".py"):
    func_name += ".py"

if os.getenv("LLM_FUNCTION_DECLARATE"):
    module = load_module(func_name)
    declarate = getattr(module, 'declarate')
    print(json.dumps(declarate(), indent=2))
else:
    data = None
    try:
        data = json.loads(os.getenv("LLM_FUNCTION_DATA"))
    except (json.JSONDecodeError, TypeError):
        print("Invalid LLM_FUNCTION_DATA")
        sys.exit(1)

    module = load_module(func_name)
    execute = getattr(module, 'execute')
    execute(data)