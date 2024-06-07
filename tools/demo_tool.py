from typing import List, Literal, Optional


def main(
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
    print(f"boolean: {boolean}")
    print(f"string: {string}")
    print(f"string_enum: {string_enum}")
    print(f"integer: {integer}")
    print(f"number: {number}")
    print(f"array: {array}")
    print(f"string_optional: {string_optional}")
    print(f"array_optional: {array_optional}")
