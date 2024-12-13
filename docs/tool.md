# Tool

This document guides you on creating custom tools for the LLM Functions framework in Bash, JavaScript, and Python.

## Definition via Comments

The key to defining the parameters your tool accepts is through specially formatted comments within your tool's source code.
The `Argcfile.sh` uses these comments to automatically generate the function declaration used by the LLM.

### Json Schema

The following JSON schema includes various types of properties. We will use this as an example to see how to write comments in each language so they can be automatically generated.

```json
{
  "name": "demo",
  "description": "Demonstrate how to create a tool using Javascript and how to use comments.",
  "parameters": {
    "type": "object",
    "properties": {
      "string": {
        "type": "string",
        "description": "Define a required string property"
      },
      "string_enum": {
        "type": "string",
        "enum": [
          "foo",
          "bar"
        ],
        "description": "Define a required string property with enum"
      },
      "string_optional": {
        "type": "string",
        "description": "Define a optional string property"
      },
      "boolean": {
        "type": "boolean",
        "description": "Define a required boolean property"
      },
      "integer": {
        "type": "integer",
        "description": "Define a required integer property"
      },
      "number": {
        "type": "number",
        "description": "Define a required number property"
      },
      "array": {
        "type": "array",
        "items": {
          "type": "string"
        },
        "description": "Define a required string array property"
      },
      "array_optional": {
        "type": "array",
        "items": {
          "type": "string"
        },
        "description": "Define a optional string array property"
      }
    },
    "required": [
      "string",
      "string_enum",
      "boolean",
      "integer",
      "number",
      "array"
    ]
  }
}
```

### Bash

Use `# @describe`, `# @option`, and `# @flag` comments to define your tool's parameters.

* `# @describe <description>`: A brief description of your tool's functionality.  This is required.

* `# @option --<option-name>[!<type>][<constraints>] <description>`:  Defines an option.
    * `--<option-name>`: The name of the option (use kebab-case).
    * `!`: Indicates a required option.
    * `<type>`:  The data type (e.g., `INT`, `NUM`, `<enum>`).  If omitted, defaults to `STRING`.
    * `<constraints>`:  Any constraints (e.g., `[foo|bar]` for an enum).
    * `<description>`: A description of the option.

* `# @flag --<flag-name> <description>`: Defines a boolean flag.
    * `--<flag-name>`: The name of the flag (use kebab-case).
    * `<description>`: A description of the flag.

**Example ([tools/demo_sh.sh](https://github.com/sigoden/llm-functions/blob/main/tools/demo_sh.sh)):**

```bash
#!/usr/bin/env bash
set -e

# @describe Demonstrate how to create a tool using Bash and how to use comment tags.
# @option --string!                  Define a required string property
# @option --string-enum![foo|bar]    Define a required string property with enum
# @option --string-optional          Define a optional string property
# @flag --boolean                    Define a boolean property
# @option --integer! <INT>           Define a required integer property
# @option --number! <NUM>            Define a required number property
# @option --array+ <VALUE>           Define a required string array property
# @option --array-optional*          Define a optional string array property

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    # ... your bash code ...
}

eval "$(argc --argc-eval "$0" "$@")"
```

### JavaScript

Use JSDoc-style comments to define your tool's parameters. The `@typedef` block defines the argument object, and each property within that object represents a parameter.

* `/** ... */`: JSDoc comment block containing the description and parameter definitions.
* `@typedef {Object} Args`: Defines the type of the argument object.
* `@property {<type>} <name> <description>`: Defines a property (parameter) of the `Args` object.
    * `<type>`: The data type (e.g., `string`, `boolean`, `number`, `string[]`, `{foo|bar}`).
    * `<name>`: The name of the parameter.
    * `<description>`: A description of the parameter.
    * `[]`: Indicates an optional parameter.

**Example ([tools/demo_js.js](https://github.com/sigoden/llm-functions/blob/main/tools/demo_js.js)):**

```javascript
/**
 * Demonstrate how to create a tool using Javascript and how to use comments.
 * @typedef {Object} Args
 * @property {string} string - Define a required string property
 * @property {'foo'|'bar'} string_enum - Define a required string property with enum
 * @property {string} [string_optional] - Define a optional string property
 * @property {boolean} boolean - Define a required boolean property
 * @property {Integer} integer - Define a required integer property
 * @property {number} number - Define a required number property
 * @property {string[]} array - Define a required string array property
 * @property {string[]} [array_optional] - Define a optional string array property
 * @param {Args} args
 */
exports.run = function (args) {
  // ... your JavaScript code ...
}
```

Of course, you can also use ESM `export` expressions to export functions.
```js
export function run() {
  // ... your JavaScript code ...
}
```

### Python

Use type hints and docstrings to define your tool's parameters.

* `def run(...)`: Function definition.
* `<type> <parameter_name>: <description>`: Type hints with descriptions in the docstring.
    * `<type>`: The data type (e.g., `str`, `bool`, `int`, `float`, `List[str]`, `Literal["foo", "bar"]`).
    * `<parameter_name>`: The name of the parameter.
    * `<description>`: Description of the parameter.
* `Optional[...]`: Indicates an optional parameter.

**Example ([tools/demo_py.py](https://github.com/sigoden/llm-functions/blob/main/tools/demo_py.py)):**

```python
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
    # ... your Python code ...
```

## Quickly create tools

`Argcfile.sh` provides a tool to quickly create script tools.

```
$ argc create@tool --help
Create a boilplate tool script

Examples:
  ./scripts/create-tool.sh _test.py foo bar! baz+ qux*

USAGE: create-tool [OPTIONS] <NAME> [PARAMS]...

ARGS:
  <NAME>       The script file name.
  [PARAMS]...  The script parameters

OPTIONS:
      --description <TEXT>  The tool description
      --force               Override the exist tool file
  -h, --help                Print help
  -V, --version             Print version
```

```sh
argc create@tool foo bar! baz+ qux*
```

The suffixes after property names represent different meanings.

- `!`: The property is required.
- `*`: The property value must be an array.
- `+`: The property is required, and its value must be an array.
- no suffix: The property is optional.

