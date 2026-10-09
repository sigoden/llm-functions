#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from typing import Any, Dict


def has_valid_description(tool_path: str) -> bool:
    """Check if the tool has a valid description comment or docstring."""
    try:
        with open(tool_path, encoding="utf-8") as f:
            content = f.read()
            if "@describe" in content:
                return True
            if tool_path.endswith(".py"):
                # Check for standard Python docstrings
                return '"""' in content or "'''" in content
            elif tool_path.endswith(".js"):
                # Check for JSDoc comments
                return "/**" in content
            return False
    except Exception:
        return False


def has_main_function(tool_path: str) -> bool:
    """Check if the tool has a main or run function."""
    try:
        with open(tool_path, encoding="utf-8") as f:
            content = f.read()
            if tool_path.endswith(".sh"):
                return (
                    "main()" in content
                    or "main ()" in content
                    or "function main" in content
                )
            elif tool_path.endswith(".py"):
                return "def run(" in content or "def main(" in content
            elif tool_path.endswith(".js"):
                return (
                    "exports.run =" in content
                    or "exports.run=" in content
                    or "function main(" in content
                )
            return False
    except Exception:
        return False


def validate_tool(tool_path: str) -> Dict[str, Any]:
    """Validate tool structure and syntax."""
    errors = []

    if not os.path.exists(tool_path):
        return {"valid": False, "errors": ["File not found"]}

    # Check required elements
    if not has_valid_description(tool_path):
        errors.append(
            "Missing description (needs @describe comment, Python docstring, or JSDoc block)"
        )

    if not has_main_function(tool_path):
        errors.append("Missing main/run function")

    # Syntax check
    if tool_path.endswith(".py"):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", tool_path], capture_output=True
        )
        if result.returncode != 0:
            errors.append(f"Python syntax error: {result.stderr.decode().strip()}")
    elif tool_path.endswith(".sh"):
        result = subprocess.run(["bash", "-n", tool_path], capture_output=True)
        if result.returncode != 0:
            errors.append(f"Bash syntax error: {result.stderr.decode().strip()}")
    elif tool_path.endswith(".js"):
        result = subprocess.run(["node", "--check", tool_path], capture_output=True)
        if result.returncode != 0:
            errors.append(f"Node.js syntax error: {result.stderr.decode().strip()}")

    return {"valid": len(errors) == 0, "errors": errors}


def main():
    root_dir = os.environ.get(
        "LLM_ROOT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    tools_dir = os.path.join(root_dir, "tools")

    if not os.path.isdir(tools_dir):
        print(f"Error: Tools directory not found at {tools_dir}")
        sys.exit(1)

    all_results = {}
    valid_count = 0
    total_count = 0

    for filename in os.listdir(tools_dir):
        if filename.endswith((".sh", ".py", ".js")) and os.path.isfile(
            os.path.join(tools_dir, filename)
        ):
            total_count += 1
            path = os.path.join(tools_dir, filename)
            result = validate_tool(path)
            all_results[filename] = result
            if result["valid"]:
                valid_count += 1

    print(
        json.dumps(
            {
                "summary": {
                    "total": total_count,
                    "valid": valid_count,
                    "invalid": total_count - valid_count,
                },
                "details": all_results,
            },
            indent=2,
        )
    )

    if valid_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
