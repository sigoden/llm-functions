# LLM Functions

This project allows you to enhance large language models (LLMs) with custom functions written in Bash/Js/Python/Ruby. Imagine your LLM being able to execute system commands, access web APIs, or perform other complex tasks – all triggered by simple, natural language prompts.

## Prerequisites

Make sure you have the following tools installed:

- [argc](https://github.com/sigoden/argc): A bash command-line framewrok and command runner
- [jq](https://github.com/jqlang/jq): A JSON processor
- [curl](https://curl.se): A command-line tool for transferring data with URLs 

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

![image](https://github.com/sigoden/llm-functions/assets/4012553/867b7b2a-25fb-4c74-9b66-3701eaabbd1f)

## Function Types

### Retrieve Type

The function returns JSON data to LLM for further processing.

AIChat does not ask permission to run the function or print the output.

### Execute Type

The function does not return data to LLM. Instead, they enable more complex actions, such as showing a progress bar or running a TUI application.

AIChat will ask permission before running the function.

![image](https://github.com/sigoden/aichat/assets/4012553/711067b8-dd23-443d-840a-5556697ab075)

**AIChat categorizes functions starting with `may_` as `execute type` and all others as `retrieve type`.**

## Writing Your Own Functions

The project supports write functions in bash/js/python.

### Bash

Create a new bashscript (.e.g. `may_execute_command.sh`) in the [./sh](./sh/) directory. 

```sh
#!/usr/bin/env bash
set -e

# @describe Executes a shell command.
# @option --command~ Command to execute, such as `ls -la`

main() {
    eval $argc_shell_command
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

Create a new javascript (.e.g. `may_execute_command.js`) in the [./js](./js/) directory. 

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

Create a new python script in the [./py](./py/) directory (e.g., `may_execute_py_code.py`).

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

Create a new ruby script in the [./rb](./rb/) directory (e.g., `may_execute_rb_code.rb`).

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