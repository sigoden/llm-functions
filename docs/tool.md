# Tool

This document guides you on creating custom tools for the LLM Functions framework in Bash, JavaScript, and Python.

## Defining Tool Parameters

To define the parameters that your tool accepts, you will use specially formatted comments within your tool's source code.
The `Argcfile.sh` script utilizes these comments to automatically generate the function declarations needed by the LLM.

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

```sh file=tools/demo_sh.sh
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

```js file=tools/demo_js.js
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

```py file=tools/demo_py.py
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
## Common tools

Common tools can be found in `tools/<tool-name>.{sh,js,py}`. Each script defines a single tool.

## Agent tools

Agents can possess their own toolset scripts located under `agents/<agent-name>/tools.{sh,js,py}`, which can contain multiple tool functions.

The following is an example of git agent:

### Bash

```sh file=agents/git/tools.sh
# @cmd Shows the working tree status
git_status() {
    # ... your bash code ...
}

# @cmd Shows differences between branches or commits
# @option --target!   Shows differences between branches or commits 
git_diff() {
    # ... your bash code ...
}

eval "$(argc --argc-eval "$0" "$@")"
```

> In `tools/<tool-name>.sh`, we use the `@describe` comment tag and a single `main` function, since it has only one function and no subcommands.
> In `agent/<agent-name>/tools.sh`, we use the `@cmd` comment tag and named functions, since it can have multiple tool functions.

### JavaScript

```js file=agents/git/tools.js
/**
 * Shows the working tree status
 */
exports.git_status = function() {
  // ... your JavaScript code ...
}

/**
 * Shows differences between branches or commits
 * @typedef {Object} Args
 * @property {string} target - Shows differences between branches or commits 
 * @param {Args} args
 */
exports.git_diff = function() {
  // ... your JavaScript code ...
}
```

### Python

```py file=agents/git/tools.py
def git_status():
    """Shows the working tree status"""
    # ... your Python code ...


def git_diff(target: str):
    """Shows differences between branches or commits
    Args:
      target: Shows differences between branches or commits 
    """
    # ... your Python code ...
```

## Quickly Create Tools

### Use argc

`Argcfile.sh` provides a tool `create@tool` to quickly create tool scripts.

```sh
argc create@tool _test.sh foo bar! baz+ qux*
```

The argument details

- `_test.sh`: The name of the tool script you want to create. The file extension can only be `.sh`, `.js`, or `.py`.
- `foo bar! baz+ qux*`: The parameters for the tool.

The suffixes attached to the tool's parameters define their characteristics:

- `!`: Indicates that the property is required.
- `*`: Specifies that the property value should be an array.
- `+`: Marks the property as required, with the value also needing to be an array.
- No suffix: Denotes that the property is optional.

### Use aichat

AI is smart enough to automatically create tool scripts for us. We just need to provide the documentation and describe the requirements well.

Use aichat to create a common tool script:
```
aichat -f docs/tool.md <<-'EOF'
create tools/get_youtube_transcript.py

description: Extract transcripts from YouTube videos
parameters:
   url (required): YouTube video URL or video ID
   lang (default: "en"): Language code for transcript (e.g., "ko", "en")
EOF
```

Use aichat to create a agent tools script:
```
aichat -f docs/agent.md -f docs/tool.md <<-'EOF'

create a spotify agent

index.yaml:
    name: spotify
    description: An AI agent that works with Spotify

tools.py:
  search: Search for tracks, albums, artists, or playlists on Spotify
    query (required): Query term
    qtype (default: "track"): Type of items to search for (track, album, artist, playlist, or comma-separated combination)
    limit (default: 10): Maximum number of items to return
  get_info: Get detailed information about a Spotify item (track, album, artist, or playlist)
    item_id (required): ID of the item to get information about
    qtype (default: "track"): Type of item: 'track', 'album', 'artist', or 'playlist'
  get_queue: Get the playback queue
  add_queue: Add tracks to the playback queue
    track_id (required): Track ID to add to queue
  get_track: Get information about user's current track
  start: Starts of resumes playback
    track_id (required): Specifies track to play
  pause: Pauses current playback
  skip: Skips current track
    num_skips (default: 1): Number of tracks to skip
EOF
```