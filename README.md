# LLM Functions

This project allows you to enhance large language models (LLMs) with custom functions written in bash/js/python. Imagine your LLM being able to execute system commands, access web APIs, or perform other complex tasks – all triggered by simple, natural language prompts.

## Prerequisites

Make sure you have the following tools installed:

- [argc](https://github.com/sigoden/argc): A bash command-line framewrok and command runner
- [jq](https://github.com/jqlang/jq): A JSON processor

## Getting Started with AIChat

**1. Clone the repository:**

```sh
git clone https://github.com/sigoden/llm-functions
```

**2. Build function declarations file and bin dir:**

First, create a `./tools.txt` file with each tool name on a new line.

Then, run `argc build` to build declarations file (`./functions.json`) and binaries dir (`./bin/`).

**3. Configure your AIChat:**

Symlink this repo directory to aichat **functions_dir**:

```sh
ln -s "$(pwd)" "$(aichat --info | grep functions_dir | awk '{print $2}')"
# OR
argc install
```

Don't forget to add the following config to your AIChat `config.yaml` file:

```yaml
function_calling: true
```

AIChat will automatically load `functions.json` and execute commands located in the `./bin` directory based on your prompts.

**4. Start using your functions:**

Now you can interact with your LLM using natural language prompts that trigger your defined functions.

![function-showcase](https://github.com/sigoden/llm-functions/assets/4012553/391867dd-577c-4aaa-9ff2-c9e67fb0f3a3)


## Function Types

### Retrieve Type

The function returns JSON data to LLM for further processing.

AIChat does not ask permission to run the function or print the output.

![retrieve-type-showcase](https://github.com/sigoden/llm-functions/assets/4012553/7e628834-9863-444a-bad8-7b51bfb18dff)

### Execute Type

The function does not have to return JSON data.

The function can perform dangerous tasks like creating/deleting files, changing network adapter, and setting a scheduled task...

AIChat will ask permission before running the function.

![execute-type-showcase](https://github.com/sigoden/llm-functions/assets/4012553/1dbc345f-daf9-4d65-a49f-3df8c7df1727)

**AIChat categorizes functions starting with `may_` as `execute type` and all others as `retrieve type`.**

## Writing Your Own Functions

You can write functions in bash/javascript/python.

`llm-functions` will automatic generate function declarations from comments. Refer to `tools/demo_tool.{sh,js,py}` for examples of how to use comments for autogeneration of declarations.

### Bash

Create a new bashscript in the [./tools/](./tools/) directory (.e.g. `may_execute_command.sh`).

```sh
#!/usr/bin/env bash
set -e

# @describe Runs a shell command.
# @option --command! The command to execute.

main() {
    eval "$argc_command"
}

eval "$(argc --argc-eval "$0" "$@")"
```

### Javascript

Create a new javascript in the [./tools/](./tools/) directory (.e.g. `may_execute_js_code.js`).

```js
/**
 * Runs the javascript code in node.js.
 * @typedef {Object} Args
 * @property {string} code - Javascript code to execute, such as `console.log("hello world")`
 * @param {Args} args
 */
exports.main = function main({ code }) {
  eval(code);
}

```

### Python

Create a new python script in the [./tools/](./tools/) directory (e.g., `may_execute_py_code.py`).

```py
def main(code: str):
    """Runs the python code.
    Args:
        code: Python code to execute, such as `print("hello world")`
    """
    exec(code)

```

## License

The project is under the MIT License, Refer to the [LICENSE](https://github.com/sigoden/llm-functions/blob/main/LICENSE) file for detailed information.