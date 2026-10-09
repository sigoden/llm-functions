#!/usr/bin/env python3
# ==============================================================================
# demo_py.py — Python Tool Demo
#
# Demonstrates how to create a tool using Python with various parameter types.
# Supports: required/optional strings, enums, integers, numbers, booleans, arrays.
#
# @describe Demonstrate how to create a tool using Python
# @option --string! <TEXT> Define a required string property
# @option --string-enum! <ENUM> Define a required string property with enum (foo|bar)
# @option --boolean Define a boolean property
# @option --integer! <NUM> Define a required integer property
# @option --number! <NUM> Define a required number property
# @option --array! <TEXT> Define a required string array property (repeatable)
# @option --string-optional <TEXT> Define an optional string property
# @option --array-optional <TEXT> Define an optional string array property (repeatable)

import os
from typing import List, Literal, Optional


def run(
    string: str,
    string_enum: Literal["foo", "bar"],
    boolean: bool = False,
    integer: int = 0,
    number: float = 0.0,
    array: Optional[List[str]] = None,
    string_optional: Optional[str] = None,
    array_optional: Optional[List[str]] = None,
) -> dict:
    """
    Demonstrate how to create a tool using Python.
    """
    output = {
        "string": string,
        "string_enum": string_enum,
        "string_optional": string_optional,
        "boolean": boolean,
        "integer": integer,
        "number": number,
        "array": array or [],
        "array_optional": array_optional or [],
    }

    # Include LLM_ environment variables
    for key, value in os.environ.items():
        if key.startswith("LLM_"):
            output[key] = value

    return output


if __name__ == "__main__":
    import json

    print(
        json.dumps(
            run(
                {
                    "string": "test",
                    "string_enum": "foo",
                    "boolean": True,
                    "integer": 42,
                    "number": 3.14,
                    "array": ["a", "b"],
                }
            ),
            indent=2,
        )
    )
