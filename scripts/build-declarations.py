#!/usr/bin/env python3
# ==============================================================================
# build-declarations.py — JSON Declaration Generator for AIChat Tool Functions
#
# Parses Python docstrings, header comments (@describe, @option, @flag), and AST
# type hints to create JSON declarations describing available tool functions.
#
# Usage: ./build-declarations.py <script-file>
# Example: ./build-declarations.py tools/sqlite_intel.py
# ==============================================================================

import ast
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

TOOL_ENTRY_FUNC = "run"

# Extended type mapping from comment <TYPE> placeholders to JSON Schema types
TYPE_MAP = {
    # Integers
    "NUM": "integer",
    "NUMBER": "integer",
    "INT": "integer",
    "INTEGER": "integer",
    "COUNT": "integer",
    "LINES": "integer",
    "LIMIT": "integer",
    # Numbers / Floats
    "SEC": "number",
    "SECS": "number",
    "SECONDS": "number",
    "FLOAT": "number",
    "DOUBLE": "number",
    "TIMEOUT": "number",
    # Booleans
    "BOOL": "boolean",
    "BOOLEAN": "boolean",
    "FLAG": "boolean",
    # Objects / Dictionaries
    "DICT": "object",
    "OBJECT": "object",
    "MAP": "object",
    "HASH": "object",
    "JSON_OBJECT": "object",
    # Arrays / Lists
    "ARRAY": "array",
    "LIST": "array",
    # Strings (default)
    "TEXT": "string",
    "STR": "string",
    "STRING": "string",
    "PATH": "string",
    "FILE": "string",
    "DIR": "string",
    "URL": "string",
    "SQL": "string",
    "PATTERN": "string",
    "KEY=VAL": "string",
    "KEY=VALUE": "string",
    "JSON": "string",
    "FORMAT": "string",
    "ENUM": "string",
    "MODE": "string",
    "LANG": "string",
    "CMD": "string",
    "METHOD": "string",
    "ACTION": "string",
}


def main():
    if len(sys.argv) < 2:
        print("Usage: ./build-declarations.py <script-file>", file=sys.stderr)
        sys.exit(1)

    scriptfile = sys.argv[1]
    script_path = Path(scriptfile).resolve()
    is_tool = script_path.parent.name == "tools" or "tools" in str(script_path.parent)

    try:
        with open(scriptfile, encoding="utf-8") as f:
            contents = f.read()
    except FileNotFoundError:
        print(f"Error: File not found '{scriptfile}'", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file '{scriptfile}': {e}", file=sys.stderr)
        sys.exit(1)

    try:
        declarations = extract_from_comments(contents, scriptfile)
        ast_functions = extract_functions(contents, is_tool)

        # Enrich comment declarations with AST type annotations (e.g. Literals -> Enums)
        if declarations and ast_functions:
            enrich_declarations_with_ast(declarations[0], ast_functions)
        elif not declarations and ast_functions:
            for func_name, docstring, func_args in ast_functions:
                description, params = parse_docstring(docstring)
                if not description:
                    continue
                declarations.append(
                    build_declaration(func_name, description, params, func_args)
                )

    except SyntaxError as e:
        print(f"Syntax error parsing '{scriptfile}': {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error parsing script '{scriptfile}': {e}", file=sys.stderr)
        sys.exit(1)

    if is_tool and declarations:
        name = os.path.splitext(os.path.basename(scriptfile))[0]
        declarations[0]["name"] = name

    print(json.dumps(declarations, indent=2))


def extract_from_comments(contents: str, scriptfile: str) -> List[Dict[str, Any]]:
    lines = contents.splitlines()
    description = ""
    properties: Dict[str, Dict[str, Any]] = {}
    required: List[str] = []
    # True while plain comment lines still extend the @describe text
    # (mirrors argc behaviour where continuation lines join the description).
    in_describe = False

    for line in lines:
        if not line.startswith("#"):
            if description:
                break
            continue

        line = line[1:].strip()
        if line.startswith("@describe"):
            description = line[len("@describe") :].strip()
            in_describe = True

        elif line.startswith("@"):
            # Any other annotation (@option/@flag/@meta/@env/...) ends the
            # description continuation.
            in_describe = False

            if line.startswith("@option"):
                match = re.match(
                    r"^@option\s+(?:-[a-zA-Z],\s*)?--([\w-]+)(!)?(?:=\S*)?\s+(?:<([^>]+)>)?\s*(.*)$",
                    line,
                )
                if match:
                    opt_name = match.group(1).replace("-", "_")
                    is_req = bool(match.group(2))
                    opt_type = match.group(3) or "string"
                    opt_desc = match.group(4).strip()

                    json_type = TYPE_MAP.get(opt_type.upper(), "string")
                    prop_def: Dict[str, Any] = {"type": json_type, "description": opt_desc}

                    enum_match = re.search(r"\b(?:choices|allowed|enum):\s*([a-zA-Z0-9_\-\/,\s]+)", opt_desc, re.I)
                    if enum_match:
                        raw_choices = re.split(r"[\/,\s]+", enum_match.group(1).strip())
                        choices = [c.strip() for c in raw_choices if c.strip()]
                        if len(choices) > 1:
                            prop_def["enum"] = choices

                    properties[opt_name] = prop_def
                    if is_req:
                        required.append(opt_name)

            elif line.startswith("@flag"):
                match = re.match(r"^@flag\s+(?:-[a-zA-Z],\s*)?--([\w-]+)\s*(.*)$", line)
                if match:
                    opt_name = match.group(1).replace("-", "_")
                    opt_desc = match.group(2).strip()
                    properties[opt_name] = {"type": "boolean", "description": opt_desc}

        elif in_describe:
            # Plain comment line directly after @describe: continuation text
            # (banner/separator or empty '#' lines end the description).
            if not line or re.fullmatch(r"[=\-─—━═~*_#\s]+", line):
                in_describe = False
            else:
                description = f"{description} {line}".strip()

    if not description:
        return []

    name = os.path.splitext(os.path.basename(scriptfile))[0]
    declaration = {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties},
    }
    if required:
        declaration["parameters"]["required"] = required

    return [declaration]


def extract_functions(
    contents: str, is_tool: bool
) -> List[Tuple[str, str, OrderedDict]]:
    tree = ast.parse(contents)
    output = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        func_name = node.name

        if (is_tool and func_name != TOOL_ENTRY_FUNC) or func_name.startswith("_"):
            continue

        docstring = ast.get_docstring(node) or ""
        func_args = OrderedDict()

        for arg in node.args.args:
            arg_name = arg.arg
            if arg_name in ("self", "cls", "kwargs"):
                continue
            arg_type = get_arg_type(arg.annotation)
            func_args[arg_name] = arg_type

        output.append((func_name, docstring, func_args))
    return output


def get_arg_type(annotation: Optional[ast.AST]) -> Any:
    if annotation is None:
        return "any"
    elif isinstance(annotation, ast.Name):
        type_map = {
            "str": "string",
            "int": "integer",
            "float": "number",
            "bool": "boolean",
            "dict": "object",
            "Dict": "object",
            "list": "array",
            "List": "array",
            "set": "array",
            "Set": "array",
            "tuple": "array",
            "Tuple": "array",
        }
        return type_map.get(annotation.id, annotation.id)
    elif isinstance(annotation, ast.Subscript):
        if isinstance(annotation.value, ast.Name):
            type_name = annotation.value.id
            if type_name in ("Dict", "dict", "Mapping"):
                return "object"
            elif type_name in ("List", "list", "Set", "set"):
                child_type = get_arg_type(annotation.slice)
                return {"type": "array", "items": {"type": child_type if isinstance(child_type, str) else "string"}}
            elif type_name == "Literal":
                literals = []
                if isinstance(annotation.slice, ast.Tuple):
                    literals = [ast.unparse(el).strip("'\"") for el in annotation.slice.elts]
                else:
                    literals = [ast.unparse(annotation.slice).strip("'\"")]
                return {"type": "string", "enum": literals}
            elif type_name == "Optional":
                return get_arg_type(annotation.slice)
    try:
        return ast.unparse(annotation)
    except Exception:
        return "any"


def enrich_declarations_with_ast(
    declaration: Dict[str, Any], ast_functions: List[Tuple[str, str, OrderedDict]]
) -> None:
    """Enrich comment-extracted declaration properties with precise AST type info & enums."""
    if not ast_functions:
        return

    _, _, func_args = ast_functions[0]
    properties = declaration["parameters"].get("properties", {})

    for arg_name, arg_type in func_args.items():
        if arg_name not in properties:
            continue

        if isinstance(arg_type, dict):
            if "type" in arg_type:
                properties[arg_name]["type"] = arg_type["type"]
            if "enum" in arg_type:
                properties[arg_name]["enum"] = arg_type["enum"]
            if "items" in arg_type:
                properties[arg_name]["items"] = arg_type["items"]
        elif isinstance(arg_type, str) and arg_type in ("string", "integer", "number", "boolean", "object", "array"):
            properties[arg_name]["type"] = arg_type


def parse_docstring(docstring: str) -> Tuple[str, Dict[str, Tuple[str, str]]]:
    lines = docstring.splitlines()
    description = ""
    raw_params: List[str] = []
    current_section = ""

    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith("Args:"):
            current_section = "args"
            continue
        elif stripped_line.startswith("Returns:") or stripped_line.startswith("Raises:"):
            break
        elif current_section == "args":
            if re.match(r"^\s+\S+:", stripped_line) or re.match(r"^\s+- \S+", stripped_line):
                raw_params.append(stripped_line)
            elif stripped_line:
                break
        else:
            if stripped_line:
                description += f"\n{stripped_line}"

    params = {}
    for raw_param in raw_params:
        try:
            name, type_, param_description = parse_param(raw_param)
            params[name] = (type_, param_description)
        except ValueError as e:
            raise ValueError(f"Invalid parameter format: '{raw_param}'. Details: {e}")

    return description.strip(), params


def parse_param(raw_param: str) -> Tuple[str, str, str]:
    match = re.match(r"^{([^}]+)}\s*(\S+?)(?:(?: *- +| +)(\S.*))?$", raw_param)

    if not match:
        raise ValueError(f"Expected format like '{{type}} name - description', but got '{raw_param}'")

    type_full = match.group(1)
    name = match.group(2)
    description = match.group(3) or ""

    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]

    json_type = map_python_type_to_json_schema(type_full)
    return name, json_type, description.strip()


def map_python_type_to_json_schema(type_: str) -> str:
    type_lower = type_.lower()
    if type_lower in ("str", "string"):
        return "string"
    elif type_lower in ("int", "integer"):
        return "integer"
    elif type_lower in ("float", "number", "sec", "seconds"):
        return "number"
    elif type_lower in ("bool", "boolean"):
        return "boolean"
    elif type_lower in ("dict", "object", "map", "mapping") or type_lower.startswith("dict"):
        return "object"
    elif type_lower in ("list", "array", "set", "tuple") or (type_lower.startswith("list[") and type_lower.endswith("]")):
        return "array"
    return "string"


def build_declaration(
    name: str,
    description: str,
    params: Dict[str, Tuple[str, str]],
    args_types: OrderedDict[str, Any],
) -> Dict[str, Any]:
    declaration = {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {},
        },
    }
    schema = declaration["parameters"]
    required_params = []

    for arg_name, arg_hint_type in args_types.items():
        param_info = params.get(arg_name)
        param_type = arg_hint_type
        param_description = ""
        is_required = True

        if param_info:
            parsed_type, parsed_description = param_info
            if parsed_type.endswith("?"):
                is_required = False
                param_type = parsed_type[:-1]
            else:
                param_type = parsed_type
            param_description = parsed_description

        if isinstance(param_type, dict):
            property_schema = param_type
            property_schema["description"] = param_description
        else:
            json_type = map_python_type_to_json_schema(str(param_type))
            property_schema = {"type": json_type, "description": param_description}

        schema["properties"][arg_name] = property_schema
        if is_required:
            required_params.append(arg_name)

    if required_params:
        schema["required"] = required_params

    return declaration


if __name__ == "__main__":
    main()
