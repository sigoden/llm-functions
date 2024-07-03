# LLM Functions

This project allows you to enhance large language models (LLMs) with custom tools and agents developed in bash/javascript/python. Imagine your LLM being able to execute system commands, access web APIs, or perform other complex tasks – all triggered by simple, natural language prompts.

![tool-showcase](https://github.com/sigoden/llm-functions/assets/4012553/41c297cb-b3f7-4e5f-925e-a80d07684b1d)

![agent-showcase](https://github.com/sigoden/aichat/assets/4012553/7308a423-2ee5-4847-be1b-a53538bc98dc)


## Prerequisites

Make sure you have the following tools installed:

- [argc](https://github.com/sigoden/argc): A bash command-line framewrok and command runner
- [jq](https://github.com/jqlang/jq): A JSON processor

## Getting Started with AIChat

**1. Clone the repository:**

```sh
git clone https://github.com/sigoden/llm-functions
```

**2. Build tools and agents:**

- Create a `./tools.txt` file with each tool filename on a new line.

```
get_current_weather.sh
execute_command.sh
#execute_py_code.py
```

- Create a `./agents.txt` file with each agent name on a new line.

```
todo-sh
#todo-js
#todo-py
```

- Run `argc build` to build functions declarations files (`functions.json`) and binaries (`./bin`) for tools and agents.

**3. Configure your AIChat:**

Symlink this repo directory to aichat **functions_dir**:

```sh
ln -s "$(pwd)" "$(aichat --info | grep -w functions_dir | awk '{print $2}')"
# OR
argc install
```

AIChat will automatically load `functions.json` and execute commands located in the `./bin` directory based on your prompts.

**4. Start using your functions:**

Now you can interact with your LLM using natural language prompts that trigger your defined functions.

## Writing Your Own Tools

Writing tools is super easy, you only need to write functions with comments.

`llm-functions` will automatically generate binaries, function declarations, and so on

Refer to `./tools/demo_tool.{sh,js,py}` for examples of how to use comments for autogeneration of declarations.

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

## Writing Agents 

Agent = Prompt + Tools (Function Callings) + Knowndge (RAG). It's also known as OpenAI's GPTs.

The agent has the following folder structure:
```
└── agents
    └── myagent
        ├── functions.json                  # Function declarations file (Auto-generated)
        ├── index.yaml                      # Agent definition file
        └── tools.{sh,js,py}                # Agent tools script
```

The agent definition file (`index.yaml`) defines crucial aspects of your agent:

```yaml
name: TestAgent                             
description: This is test agent
version: v0.1.0
instructions: You are a test ai agent to ... 
conversation_starters:
  - What can you do?
documents:
  - files/doc.pdf
```

Refer to `./agents/todo-{sh,js,py}` for examples of how to implement a agent.

## License

The project is under the MIT License, Refer to the [LICENSE](https://github.com/sigoden/llm-functions/blob/main/LICENSE) file for detailed information.