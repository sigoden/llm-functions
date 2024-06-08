#!/usr/bin/env python

import ast
import os
import json
import re
import sys
from collections import OrderedDict

TOOL_ENTRY_FUNC = "run"


def main(is_tool=True):
    scriptfile = sys.argv[1]
    is_tool = os.path.dirname(scriptfile) == "tools"

    with open(scriptfile, "r", encoding="utf-8") as f:
        contents = f.read()

    functions = extract_functions(contents, is_tool)
    declarations = []
    for function in functions:
        func_name, docstring, func_args = function
        description, params = parse_docstring(docstring)
        declarations.append(
            build_declaration(func_name, description, params, func_args)
        )

    if is_tool:
        name = os.path.splitext(os.path.basename(scriptfile))[0]
        if declarations:
            declarations = declarations[0:1]
            declarations[0]["name"] = name

    print(json.dumps(declarations, indent=2))


def extract_functions(contents: str, is_tool: bool):
    tree = ast.parse(contents)
    output = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        func_name = node.name
        if func_name.startswith("_"):
            continue
        if is_tool and func_name != TOOL_ENTRY_FUNC:
            continue
        docstring = ast.get_docstring(node) or ""
        func_args = OrderedDict()
        for arg in node.args.args:
            arg_name = arg.arg
            arg_type = get_arg_type(arg.annotation)
            func_args[arg_name] = arg_type
        output.append((func_name, docstring, func_args))
    return output


def get_arg_type(annotation) -> str:
    if annotation is None:
        return ""
    elif isinstance(annotation, ast.Name):
        return annotation.id
    elif isinstance(annotation, ast.Subscript):
        if isinstance(annotation.value, ast.Name):
            type_name = annotation.value.id
            if type_name == "List":
                child = get_arg_type(annotation.slice)
                return f"list[{child}]"
            if type_name == "Literal":
                literals = [ast.unparse(el) for el in annotation.slice.elts]
                return f"{'|'.join(literals)}"
            if type_name == "Optional":
                child = get_arg_type(annotation.slice)
                return f"{child}?"
    return "any"


def parse_docstring(docstring: str):
    lines = docstring.splitlines()
    description = ""
    rawParams = []
    is_in_args = False
    for line in lines:
        if not is_in_args:
            if line.startswith("Args:"):
                is_in_args = True
            else:
                description += f"\n{line}"
            continue
        else:
            if re.search(r"^\s+", line):
                rawParams.append(line.strip())
            else:
                break
    params = {}
    for rawParam in rawParams:
        name, type_, param_description = parse_param(rawParam)
        params[name] = (type_, param_description)
    return (description.strip(), params)


def parse_param(raw_param: str):
    name = ""
    description = ""
    type_from_comment = ""
    if ":" in raw_param:
        name, description = raw_param.split(":", 1)
        name = name.strip()
        description = description.strip()
    else:
        name = raw_param
    if " " in name:
        name, type_from_comment = name.split(" ", 1)
        type_from_comment = type_from_comment.strip()

    if type_from_comment.startswith("(") and type_from_comment.endswith(")"):
        type_from_comment = type_from_comment[1:-1]
    type_parts = [value.strip() for value in type_from_comment.split(",")]
    type_ = type_parts[0]
    if "optional" in type_parts[1:]:
        type_ = f"{type_}?"

    return (name, type_, description)


def build_declaration(
    name: str, description: str, params: dict, args: OrderedDict[str, str]
) -> dict[str, dict]:
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
    for arg_name, arg_type in args.items():
        type_ = arg_type
        description = ""
        required = True
        if params.get(arg_name):
            param_type, description = params[arg_name]
            if not type_:
                type_ = param_type
        if type_.endswith("?"):
            type_ = type_[:-1]
            required = False
        try:
            property = build_property(type_, description)
        except:
            raise ValueError(f"Unable to parse arg '{arg_name}' of function '{name}'")
        schema["properties"][arg_name] = property
        if required:
            required_params.append(arg_name)
    if required_params:
        schema["required"] = required_params
    return declaration


def build_property(type_: str, description: str):
    property = {}
    if "|" in type_:
        property["type"] = "string"
        property["enum"] = type_.replace("'", "").split("|")
    elif type_ == "bool":
        property["type"] = "boolean"
    elif type_ == "str":
        property["type"] = "string"
    elif type_ == "int":
        property["type"] = "integer"
    elif type_ == "float":
        property["type"] = "number"
    elif type_ == "list[str]":
        property["type"] = "array"
        property["items"] = {"type": "string"}
    elif type_ == "":
        property["type"] = "string"
    else:
        raise ValueError(f"Unsupported type `{type_}`")
    property["description"] = description
    return property


if __name__ == "__main__":
    main()
