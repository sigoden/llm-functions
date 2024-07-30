# LLM Functions

This project allows you to enhance large language models (LLMs) with custom tools and agents developed in bash/javascript/python. Imagine your LLM being able to execute system commands, access web APIs, or perform other complex tasks – all triggered by simple, natural language prompts.

![tool-showcase](https://github.com/sigoden/llm-functions/assets/4012553/41c297cb-b3f7-4e5f-925e-a80d07684b1d)

![agent-showcase](https://github.com/sigoden/aichat/assets/4012553/7308a423-2ee5-4847-be1b-a53538bc98dc)


## Prerequisites

Make sure you have the following tools installed:

- [argc](https://github.com/sigoden/argc): A bash command-line framewrok and command runner
- [jq](https://github.com/jqlang/jq): A JSON processor

## Getting Started with [AIChat](https://github.com/sigoden/aichat)

### 1. Clone the repository:

```sh
git clone https://github.com/sigoden/llm-functions
```

### 2. Build tools and agents:

**I. Create a `./tools.txt` file with each tool filename on a new line.**

```
get_current_weather.sh
#execute_command.sh
execute_py_code.py
search_tavily.sh 
``` 

**II. Create a `./agents.txt` file with each agent name on a new line.**

```
coder
todo
```

**III. Run `argc build` to build tools and agents.**

### 3. Install to AIChat:

Symlink this repo directory to AIChat **functions_dir**:

```sh
ln -s "$(pwd)" "$(aichat --info | grep -w functions_dir | awk '{print $2}')"
# OR
argc install
```

### 4. Start using the functions:

Done! You can experience the magic of `llm-functions` in AIChat.

## Writing Your Own Tools

Building tools for our platform is remarkably straightforward. You can leverage your existing programming knowledge, as tools are essentially just functions written in your preferred language.

LLM Functions automatically generates the JSON declarations for the tools based on **comments**. Refer to `./tools/demo_tool.{sh,js,py}` for examples of how to use comments for autogeneration of declarations.

### Bash

Create a new bashscript in the [./tools/](./tools/) directory (.e.g. `may_execute_command.sh`).

```sh
#!/usr/bin/env bash
set -e

# @describe Runs a shell command.
# @option --command! The command to execute.

main() {
    eval "$argc_command" >> "$LLM_OUTPUT"
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
  return eval(code);
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
    return exec(code)

```

## Writing Your Own Agents

Agent = Prompt + Tools (Function Callings) + Knowndge (RAG). It's also known as OpenAI's GPTs.

The agent has the following folder structure:
```
└── agents
    └── myagent
        ├── functions.json                  # Function JSON declarations (Auto-generated)
        ├── index.yaml                      # Agent definition
        ├── tools.txt                       # Shared tools from ./tools
        └── tools.{sh,js,py}                # Agent tools 
```

The agent definition file (`index.yaml`) defines crucial aspects of your agent:

```yaml
name: TestAgent                             
description: This is test agent
version: 0.1.0
instructions: You are a test ai agent to ... 
conversation_starters:
  - What can you do?
documents:
  - local-file.txt
  - local-dir/
  - https://example.com/remote-file.txt
```

Refer to [./agents/demo](https://github.com/sigoden/llm-functions/tree/main/agents/demo) for examples of how to implement a agent.

## License

The project is under the MIT License, Refer to the [LICENSE](https://github.com/sigoden/llm-functions/blob/main/LICENSE) file for detailed information.