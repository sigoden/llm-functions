import os
from typing import List, Literal, Optional

def run(
    boolean: bool,
    string: str,
    string_enum: Literal["foo", "bar"],
    integer: int,
    number: float,
    array: List[str],
    string_optional: Optional[str] = None,
    array_optional: Optional[List[str]] = None,
) -> None:
    """Demonstrate how to create a tool using Python and how to use comments.
    Args:
        boolean: Define a required boolean property
        string: Define a required string property
        string_enum: Define a required string property with enum
        integer: Define a required integer property
        number: Define a required number property
        array: Define a required string array property
        string_optional: Define a optional string property
        array_optional: Define a optional string array property
    """
    for key, value in locals().items():
        print(f"{key}: {value}")

    for key, value in os.environ.items():
        if key.startswith("LLM_"):
            print(f"{key}: {value}")