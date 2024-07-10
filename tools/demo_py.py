import os
from typing import List, Literal, Optional

def run(
    string: str,
    string_enum: Literal["foo", "bar"],
    boolean: bool,
    integer: int,
    number: float,
    array: List[str],
    string_optional: Optional[str] = None,
    array_optional: Optional[List[str]] = None,
):
    """Demonstrate how to create a tool using Python and how to use comments.
    Args:
        string: Define a required string property
        string_enum: Define a required string property with enum
        boolean: Define a required boolean property
        integer: Define a required integer property
        number: Define a required number property
        array: Define a required string array property
        string_optional: Define a optional string property
        array_optional: Define a optional string array property
    """
    output = f"""string: {string}
string_enum: {string_enum}
string_optional: {string_optional}
boolean: {boolean}
integer: {integer}
number: {number}
array: {array}
array_optional: {array_optional}"""

    for key, value in os.environ.items():
        if key.startswith("LLM_"):
            output = f"{output}\n{key}: {value}"

    return output
