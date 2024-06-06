# LLM Functions

This project allows you to enhance large language models (LLMs) with custom functions written in bash/js/python/ruby. Imagine your LLM being able to execute system commands, access web APIs, or perform other complex tasks – all triggered by simple, natural language prompts.

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

First, create a `./functions.txt` file with each function name on a new line.

Then, run `argc build` to build function declarations file `./functions.json` and bin dir `./bin/`.

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

AIChat will automatically load `functions.json` and execute functions located in the `./bin` directory based on your prompts.

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

![execute-type-showcase](https://github.com/sigoden/llm-functions/assets/4012553/bbe7f04d-4bad-49c8-b2f4-6b06290a63a4)

**AIChat categorizes functions starting with `may_` as `execute type` and all others as `retrieve type`.**

## Writing Your Own Functions

The project supports write functions in bash/js/python.

### Bash

Create a new bashscript in the [./tools/](./tools/) directory (.e.g. `may_execute_shell_command.sh`).

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

`llm-functions` will automatic generate function declaration.json from [comment tags](https://github.com/sigoden/argc?tab=readme-ov-file#comment-tags).

The relationship between comment tags and parameters in function declarations is as follows:

```sh
# @flag --boolean                   Parameter `{"type": "boolean"}`
# @option --string                  Parameter `{"type": "string"}`
# @option --string-enum[foo|bar]    Parameter `{"type": "string", "enum": ["foo", "bar"]}`
# @option --integer <INT>           Parameter `{"type": "integer"}`
# @option --number <NUM>            Parameter `{"type": "number"}`
# @option --array* <VALUE>          Parameter `{"type": "array", "items": {"type":"string"}}`
# @option --scalar-required!        Use `!` to mark a scalar parameter as required.
# @option --array-required+         Use `+` to mark a array parameter as required
```

### Javascript

Create a new javascript in the [./tools/](./tools/) directory (.e.g. `may_execute_js_code.js`).

```js
exports.declarate = function declarate() {
  return {
    "name": "may_execute_js_code",
    "description": "Runs the javascript code in node.js.",
    "parameters": {
      "type": "object",
      "properties": {
        "code": {
          "type": "string",
          "description": "Javascript code to execute, such as `console.log(\"hello world\")`"
        }
      },
      "required": [
        "code"
      ]
    }
  }
}

exports.execute = function execute(data) {
  eval(data.code)
}

```

### Python

Create a new python script in the [./tools/](./tools/) directory (e.g., `may_execute_py_code.py`).

```py
def declarate():
  return {
    "name": "may_execute_py_code",
    "description": "Runs the python code.",
    "parameters": {
      "type": "object",
      "properties": {
        "code": {
          "type": "string",
          "description": "python code to execute, such as `print(\"hello world\")`"
        }
      },
      "required": [
        "code"
      ]
    }
  }


def execute(data):
  exec(data["code"])
```

### Ruby

Create a new ruby script in the [./tools/](./tools/) directory (e.g., `may_execute_rb_code.rb`).

```rb
def declarate
  {
    "name": "may_execute_rb_code",
    "description": "Runs the ruby code.",
    "parameters": {
      "type": "object",
      "properties": {
        "code": {
          "type": "string",
          "description": "Ruby code to execute, such as `puts \"hello world\"`"
        }
      },
      "required": [
        "code"
      ]
    }
  }
end

def execute(data)
  eval(data["code"])
end
```

## License

The project is under the MIT License, Refer to the [LICENSE](https://github.com/sigoden/llm-functions/blob/main/LICENSE) file for detailed information.