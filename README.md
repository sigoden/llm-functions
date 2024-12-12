# LLM Functions

This project empowers you to effortlessly build powerful LLM tools and agents using familiar languages like Bash, JavaScript, and Python. 

Forget complex integrations, **harness the power of [function calling](https://platform.openai.com/docs/guides/function-calling)** to connect your LLMs directly to custom code and unlock a world of possibilities. Execute system commands, process data, interact with APIs –  the only limit is your imagination.

**Tools Showcase**
![llm-function-tool](https://github.com/user-attachments/assets/40c77413-30ba-4f0f-a2c7-19b042a1b507)

**Agents showcase**
![llm-function-agent](https://github.com/user-attachments/assets/6e380069-8211-4a16-8592-096e909b921d)

## Prerequisites

Make sure you have the following tools installed:

- [argc](https://github.com/sigoden/argc): A bash command-line framework and command runner
- [jq](https://github.com/jqlang/jq): A JSON processor

## Getting Started with [AIChat](https://github.com/sigoden/aichat)

**Currently, AIChat is the only CLI tool that supports `llm-functions`. We look forward to more tools supporting `llm-functions`.**

### 1. Clone the repository

```sh
git clone https://github.com/sigoden/llm-functions
```

### 2. Build tools and agents

#### I. Create a `./tools.txt` file with each tool filename on a new line.

```
get_current_weather.sh
execute_command.sh
#execute_py_code.py
``` 

<details>
<summary>Where is the web_search tool?</summary>
<br>

The `web_search` tool itself doesn't exist directly, Instead, you can choose from a variety of web search tools.

To use one as the `web_search` tool, follow these steps:

1. **Choose a Tool:** Available tools include:
    * `web_search_cohere.sh`
    * `web_search_perplexity.sh`
    * `web_search_tavily.sh`
    * `web_search_vertexai.sh`

2. **Link Your Choice:** Use the `argc` command to link your chosen tool as `web_search`. For example, to use `web_search_perplexity.sh`:

    ```sh
    $ argc link-web-search web_search_perplexity.sh
    ```

    This command creates a symbolic link, making `web_search.sh` point to your selected `web_search_perplexity.sh` tool. 

Now there is a `web_search.sh` ready to be added to your `./tools.txt`.

</details>

#### II. Create a `./agents.txt` file with each agent name on a new line.

```
coder
todo
```

#### III. Build `bin` and `functions.json`

```sh
argc build
```

### 3. Install to AIChat

Symlink this repo directory to AIChat's **functions_dir**:

```sh
ln -s "$(pwd)" "$(aichat --info | grep -w functions_dir | awk '{$1=""; print substr($0,2)}')"
# OR
argc install
```

### 4. Start using the functions

Done! Now you can use the tools and agents with AIChat.

```sh
aichat --role %functions% what is the weather in Paris?
aichat --agent todo list all my todos
```

## Writing Your Own Tools

Building tools for our platform is remarkably straightforward. You can leverage your existing programming knowledge, as tools are essentially just functions written in your preferred language.

LLM Functions automatically generates the JSON declarations for the tools based on **comments**. Refer to `./tools/demo_tool.{sh,js,py}` for examples of how to use comments for autogeneration of declarations.

### Bash

Create a new bashscript in the [./tools/](./tools/) directory (.e.g. `execute_command.sh`).

```sh
#!/usr/bin/env bash
set -e

# @describe Execute the shell command.
# @option --command! The command to execute.

main() {
    eval "$argc_command" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
```

### Javascript

Create a new javascript in the [./tools/](./tools/) directory (.e.g. `execute_js_code.js`).

```js
/**
 * Execute the javascript code in node.js.
 * @typedef {Object} Args
 * @property {string} code - Javascript code to execute, such as `console.log("hello world")`
 * @param {Args} args
 */
exports.run = function ({ code }) {
  eval(code);
}

```

### Python

Create a new python script in the [./tools/](./tools/) directory (e.g. `execute_py_code.py`).

```py
def run(code: str):
    """Execute the python code.
    Args:
        code: Python code to execute, such as `print("hello world")`
    """
    exec(code)

```

## Writing Your Own Agents

Agent = Prompt + Tools (Function Calling) + Documents (RAG), which is equivalent to OpenAI's GPTs.

The agent has the following folder structure:
```
└── agents
    └── myagent
        ├── functions.json                  # JSON declarations for functions (Auto-generated)
        ├── index.yaml                      # Agent definition
        ├── tools.txt                       # Shared tools
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
variables:
  - name: foo
    description: This is a foo
documents:
  - local-file.txt
  - local-dir/
  - https://example.com/remote-file.txt
```

Refer to [./agents/demo](https://github.com/sigoden/llm-functions/tree/main/agents/demo) for examples of how to implement a agent.

## MCP (Model Context Protocol)

- [mcp/server](https://github.com/sigoden/llm-functions/tree/main/mcp/server): Let LLM-Functions tools/agents be used through the Model Context Protocol. 
- [mcp/bridge](https://github.com/sigoden/llm-functions/tree/main/mcp/bridge): Let external MCP tools be used by LLM-Functions.

## License

The project is under the MIT License, Refer to the [LICENSE](https://github.com/sigoden/llm-functions/blob/main/LICENSE) file for detailed information.
