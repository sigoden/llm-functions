#!/usr/bin/env python3
"""
# @describe Generate markdown documentation for all tools.
"""

import os
import re


def extract_description(tool_path):
    try:
        with open(tool_path, encoding="utf-8") as f:
            content = f.read()
            match = re.search(r"@describe\s+(.*)", content)
            if match:
                return match.group(1).strip()
            # For JS JSDoc
            match = re.search(r"\*\s+(.*)", content)
            if match:
                return match.group(1).strip()
    except Exception:
        pass
    return "No description available"


def extract_parameters(tool_path):
    params = []
    try:
        with open(tool_path, encoding="utf-8") as f:
            for line in f:
                match = re.search(r"@option\s+--(\w+)(!?)\s+(.*)", line)
                if match:
                    name = match.group(1)
                    required = "Yes" if match.group(2) == "!" else "No"
                    desc = match.group(3).strip()
                    params.append(f"| {name} | {required} | {desc} |")
    except Exception:
        pass

    if not params:
        return "No parameters defined."

    header = "| Parameter | Required | Description |\n|-----------|----------|-------------|\n"
    return header + "\n".join(params)


def generate_tool_docs():
    root_dir = os.environ.get(
        "LLM_ROOT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    tools_dir = os.path.join(root_dir, "tools")
    docs_dir = os.path.join(root_dir, "tools/docs")
    os.makedirs(docs_dir, exist_ok=True)

    if not os.path.isdir(tools_dir):
        print(f"Tools directory not found: {tools_dir}")
        return

    for filename in os.listdir(tools_dir):
        if filename.endswith((".sh", ".py", ".js")):
            tool_name = os.path.splitext(filename)[0]
            path = os.path.join(tools_dir, filename)

            doc = f"# {tool_name}\n\n"
            doc += f"**Description:** {extract_description(path)}\n\n"
            doc += f"**Parameters:**\n\n{extract_parameters(path)}\n\n"
            doc += f"**Source File:** `{filename}`\n"

            doc_path = os.path.join(docs_dir, f"{tool_name}.md")
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write(doc)
            print(f"Generated documentation for {tool_name}")


if __name__ == "__main__":
    generate_tool_docs()
