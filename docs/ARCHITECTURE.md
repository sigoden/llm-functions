# LLM Functions Architecture

This document provides a comprehensive explanation of how all components in the llm-functions repository work together and integrate with [AIChat](https://github.com/sigoden/aichat) to expose MCP (Model Context Protocol) tools.

## Table of Contents

1. [Overview](#overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Core Components](#core-components)
4. [Data Flow](#data-flow)
5. [Tool System](#tool-system)
6. [Agent System](#agent-system)
7. [MCP Integration](#mcp-integration)
8. [AIChat Integration](#aichat-integration)
9. [Build System](#build-system)
10. [Runtime Execution](#runtime-execution)

---

## Overview

**LLM Functions** is a framework that enables developers to create custom LLM tools and agents using Bash, JavaScript, and Python. The project leverages [function calling](https://platform.openai.com/docs/guides/function-calling) to connect LLMs directly to custom code, allowing them to execute system commands, process data, and interact with APIs.

The framework provides:
- **Tools**: Reusable functions that perform specific tasks (e.g., web search, file operations)
- **Agents**: Intelligent assistants combining prompts, tools, and RAG documents (similar to OpenAI's GPTs)
- **MCP Bridge**: Bidirectional integration with the Model Context Protocol ecosystem

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                    AIChat CLI                                    │
│                          (Primary Consumer Application)                          │
└─────────────────────────────────────────────────────────────────────────────────┘
                                         │
                                         │ Function Calling
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              LLM Functions Framework                             │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                            functions.json                                 │   │
│  │              (Master index of all tool/agent declarations)                │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                         │                                        │
│              ┌──────────────────────────┼──────────────────────────┐            │
│              ▼                          ▼                          ▼            │
│  ┌───────────────────┐      ┌───────────────────┐      ┌───────────────────┐   │
│  │      /tools/      │      │     /agents/      │      │   /mcp/bridge/    │   │
│  │  Common tools in  │      │  Agent-specific   │      │  External MCP     │   │
│  │  Bash/JS/Python   │      │  configurations   │      │  tool integration │   │
│  └───────────────────┘      └───────────────────┘      └───────────────────┘   │
│              │                          │                          │            │
│              └──────────────────────────┼──────────────────────────┘            │
│                                         ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                               /bin/                                       │   │
│  │                    (Generated executable symlinks)                        │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
         ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
         │  run-tool.sh    │   │  run-agent.sh   │   │ run-mcp-tool.sh │
         │  run-tool.js    │   │  run-agent.js   │   │                 │
         │  run-tool.py    │   │  run-agent.py   │   │                 │
         └─────────────────┘   └─────────────────┘   └─────────────────┘
                    │                    │                    │
                    ▼                    ▼                    ▼
         ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
         │  Tool Scripts   │   │  Agent Tools    │   │  HTTP API Call  │
         │  in /tools/     │   │  in /agents/    │   │  to MCP Bridge  │
         └─────────────────┘   └─────────────────┘   └─────────────────┘
```

---

## Core Components

### Directory Structure

```
llm-functions/
├── Argcfile.sh              # Build system & command runner
├── functions.json           # Auto-generated tool declarations
├── tools.txt                # Enabled tools list
├── agents.txt               # Enabled agents list
├── .env                     # Environment variables
│
├── tools/                   # Common tools (Bash/JS/Python scripts)
│   ├── execute_command.sh
│   ├── execute_js_code.js
│   ├── execute_py_code.py
│   ├── fs_*.sh              # File system operations
│   ├── web_search_*.sh      # Various web search implementations
│   └── ...
│
├── agents/                  # AI Agents
│   └── <agent-name>/
│       ├── index.yaml       # Agent configuration
│       ├── tools.sh/js/py   # Agent-specific tools
│       ├── tools.txt        # Shared tools list
│       └── functions.json   # Auto-generated declarations
│
├── scripts/                 # Build and runtime scripts
│   ├── build-declarations.sh/js/py
│   ├── run-tool.sh/js/py
│   ├── run-agent.sh/js/py
│   ├── run-mcp-tool.sh
│   └── mcp.sh
│
├── mcp/                     # Model Context Protocol
│   ├── server/              # Exposes llm-functions via MCP
│   └── bridge/              # Integrates external MCP tools
│
├── bin/                     # Generated executables (symlinks)
├── cache/                   # Runtime cache directory
└── docs/                    # Documentation
```

### Configuration Files

| File | Purpose |
|------|---------|
| `tools.txt` | Lists tool filenames to include in the build (one per line, `#` for comments) |
| `agents.txt` | Lists agent names to include in the build |
| `functions.json` | Auto-generated JSON Schema declarations for all tools/agents |
| `.env` | Environment variables (API keys, configuration) |
| `mcp.json` | External MCP server configurations for the bridge |

---

## Data Flow

### Tool Invocation Flow

```
┌──────────┐   Function Call   ┌──────────────┐   Execute   ┌─────────────┐
│  AIChat  │ ─────────────────▶│  /bin/<tool> │ ──────────▶ │ run-tool.*  │
└──────────┘                   └──────────────┘             └─────────────┘
                                                                   │
                                                                   ▼
                                                            ┌─────────────┐
                                                            │ tools/*.sh  │
                                                            │ tools/*.js  │
                                                            │ tools/*.py  │
                                                            └─────────────┘
                                                                   │
                                                            Write to LLM_OUTPUT
                                                                   │
                                                                   ▼
┌──────────┐   Return Result   ┌──────────────┐    Read     ┌─────────────┐
│  AIChat  │ ◀────────────────│  /bin/<tool> │ ◀──────────│ $LLM_OUTPUT │
└──────────┘                   └──────────────┘             └─────────────┘
```

### Build Flow

```
┌──────────────┐     ┌──────────────┐     ┌───────────────────────┐
│  tools.txt   │────▶│ argc build   │────▶│ build-declarations.*  │
│  agents.txt  │     │              │     │ (parse comments)      │
└──────────────┘     └──────────────┘     └───────────────────────┘
                                                      │
                                                      ▼
                     ┌──────────────┐     ┌───────────────────────┐
                     │ /bin/<name>  │◀────│   functions.json      │
                     │ (symlinks)   │     │ (JSON Schema)         │
                     └──────────────┘     └───────────────────────┘
```

---

## Tool System

### Tool Definition

Tools are scripts that define their parameters through specially formatted comments. The build system parses these comments to generate JSON Schema declarations.

#### Bash Tool Example

```bash
#!/usr/bin/env bash
set -e

# @describe Execute the shell command.
# @option --command! The command to execute.
# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    eval "$argc_command" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
```

#### JavaScript Tool Example

```javascript
/**
 * Execute the javascript code in node.js.
 * @typedef {Object} Args
 * @property {string} code - Javascript code to execute
 * @param {Args} args
 */
exports.run = function ({ code }) {
  eval(code);
}
```

#### Python Tool Example

```python
def run(code: str):
    """Execute the python code.
    Args:
        code: Python code to execute
    """
    exec(code)
```

### Comment Tag Reference

| Language | Description | Parameters | Arrays | Required | Enum |
|----------|-------------|------------|--------|----------|------|
| **Bash** | `@describe` | `@option --name` | `--name+` (required), `--name*` (optional) | `!` suffix | `[foo\|bar]` |
| **JS** | JSDoc block | `@property {type} name` | `{string[]}` | No `[]` around name | `{'foo'\|'bar'}` |
| **Python** | Docstring + type hints | Parameter with type hint | `List[str]` | No default value | `Literal["foo", "bar"]` |

### Generated JSON Schema

The build system generates JSON Schema declarations like:

```json
{
  "name": "execute_command",
  "description": "Execute the shell command.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "description": "The command to execute."
      }
    },
    "required": ["command"]
  }
}
```

---

## Agent System

### Agent Structure

```
Agent = Prompt (Instructions) + Tools (Function Calling) + Documents (RAG)
```

Agents are defined by an `index.yaml` configuration file:

```yaml
name: Coder
description: An AI agent that assists your coding tasks
version: 0.1.0
instructions: |
  You are an exceptional software developer...

variables:
  - name: project_root
    description: The root directory of the project

documents:
  - local-file.txt
  - local-dir/
  - https://example.com/remote-file.txt

conversation_starters:
  - "Create a new Python project structure"
  - "Help me debug this error"
```

### Agent Tools

Agents can have their own tools in `tools.sh`, `tools.js`, or `tools.py`:

```bash
# agents/git/tools.sh

# @cmd Shows the working tree status
git_status() {
    git status >> "$LLM_OUTPUT"
}

# @cmd Shows differences between commits
# @option --target! Target branch or commit
git_diff() {
    git diff "$argc_target" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
```

### Built-in Variables

Agents have access to built-in variables in their instructions:

| Variable | Description | Example |
|----------|-------------|---------|
| `{{__os__}}` | Operating system | linux |
| `{{__arch__}}` | Architecture | x86_64 |
| `{{__shell__}}` | Default shell | bash |
| `{{__cwd__}}` | Current directory | /home/user |
| `{{__now__}}` | ISO timestamp | 2024-12-15T10:30:00Z |
| `{{__tools__}}` | Available tools list | - |

---

## MCP Integration

The Model Context Protocol (MCP) integration is bidirectional:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            MCP Ecosystem                                         │
│                                                                                  │
│  ┌─────────────────────┐                        ┌─────────────────────┐         │
│  │  External MCP       │                        │  External MCP       │         │
│  │  Clients            │                        │  Servers            │         │
│  │  (Claude Desktop,   │                        │  (GitHub, Postgres, │         │
│  │   Cursor, etc.)     │                        │   Spotify, etc.)    │         │
│  └─────────────────────┘                        └─────────────────────┘         │
│            │                                              │                      │
│            │ MCP Protocol                                 │ MCP Protocol         │
│            ▼                                              ▼                      │
│  ┌─────────────────────┐                        ┌─────────────────────┐         │
│  │    MCP Server       │                        │    MCP Bridge       │         │
│  │  /mcp/server/       │◀──── LLM Functions ───▶│  /mcp/bridge/       │         │
│  │                     │                        │                     │         │
│  │  Exposes llm-funcs  │                        │  Imports external   │         │
│  │  tools to external  │                        │  MCP tools into     │         │
│  │  MCP clients        │                        │  llm-functions      │         │
│  └─────────────────────┘                        └─────────────────────┘         │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### MCP Server (Outbound)

The MCP Server (`/mcp/server/`) exposes llm-functions tools to external MCP clients.

**How it works:**

1. Reads `functions.json` to get tool declarations
2. Starts an MCP server using `@modelcontextprotocol/sdk`
3. Handles `ListTools` and `CallTool` requests
4. Executes tools via the `/bin/` executables
5. Returns results through MCP protocol

**Configuration (in MCP client):**

```json
{
  "mcpServers": {
    "llm-functions": {
      "command": "npx",
      "args": ["mcp-llm-functions", "/path/to/llm-functions"]
    }
  }
}
```

**Server Implementation Flow:**

```javascript
// mcp/server/index.js (simplified)

// 1. Load function declarations
const functions = JSON.parse(fs.readFileSync('functions.json'));

// 2. Create MCP server
const server = new Server({ name: "llm-functions", version: "0.1.0" });

// 3. Handle ListTools - return all tool definitions
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: functions.map(f => ({
    name: f.name,
    description: f.description,
    inputSchema: f.parameters
  }))
}));

// 4. Handle CallTool - execute and return result
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { exitCode, output } = await runCommand(
    request.params.name,
    [JSON.stringify(request.params.arguments)]
  );
  return { content: [{ type: "text", text: output }] };
});
```

### MCP Bridge (Inbound)

The MCP Bridge (`/mcp/bridge/`) imports external MCP tools into llm-functions.

**How it works:**

1. Reads `mcp.json` configuration file
2. Launches configured external MCP servers as child processes
3. Connects to each server as an MCP client
4. Collects all tool definitions from external servers
5. Exposes tools via HTTP REST API (port 8808)
6. Updates `functions.json` with external tool declarations (marked with `"mcp": "<server-name>"`)

**Configuration (`mcp.json`):**

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "your-token"
      }
    },
    "postgres": {
      "command": "uvx",
      "args": ["mcp-server-postgres", "postgresql://..."]
    }
  }
}
```

**Bridge Architecture:**

```
┌────────────────────────────────────────────────────────────────────┐
│                        MCP Bridge Server                            │
│                     (Express.js on port 8808)                       │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                      HTTP REST API                           │   │
│  │  GET  /tools           → List all external tool definitions  │   │
│  │  POST /tools/:name     → Execute tool with JSON body         │   │
│  │  GET  /health          → Health check                        │   │
│  │  GET  /pid             → Get server PID                      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│              ┌───────────────┼───────────────┐                     │
│              ▼               ▼               ▼                     │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐      │
│  │  MCP Client     │ │  MCP Client     │ │  MCP Client     │      │
│  │  → GitHub       │ │  → Postgres     │ │  → Spotify      │      │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘      │
│           │                   │                   │                │
└───────────│───────────────────│───────────────────│────────────────┘
            ▼                   ▼                   ▼
   ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
   │ External MCP    │ │ External MCP    │ │ External MCP    │
   │ Server Process  │ │ Server Process  │ │ Server Process  │
   └─────────────────┘ └─────────────────┘ └─────────────────┘
```

**Tool Name Prefixing:**

External tools are prefixed with the server name to avoid collisions:
- `github` server's `create_issue` → `github_create_issue`
- `postgres` server's `query` → `postgres_query`

This can be disabled with `"prefix": false` in the configuration.

---

## AIChat Integration

### How AIChat Uses LLM Functions

[AIChat](https://github.com/sigoden/aichat) is the primary CLI tool that consumes llm-functions. The integration works through:

1. **Functions Directory**: AIChat looks for a `functions_dir` containing llm-functions
2. **Tool Discovery**: Reads `functions.json` to discover available tools
3. **Function Calling**: When the LLM requests a tool, AIChat executes it via `/bin/`
4. **Result Handling**: Tool output (from `$LLM_OUTPUT`) is returned to the LLM

### Setup Methods

**Method 1: Symlink**

```bash
# Create symlink to AIChat's functions directory
ln -s "$(pwd)" "$(aichat --info | sed -n 's/^functions_dir\s\+//p')"
# Or use the built-in command:
argc link-to-aichat
```

**Method 2: Environment Variable**

```bash
export AICHAT_FUNCTIONS_DIR="/path/to/llm-functions"
```

### Usage Examples

```bash
# Use tools with the %functions% role
aichat --role %functions% what is the weather in Paris?

# Use a specific agent
aichat --agent coder help me create a Python web app

# List available tools/agents
aichat --list-functions
aichat --list-agents
```

### Execution Flow in AIChat

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                  AIChat                                          │
│                                                                                  │
│  1. User Input: "What's the weather in Paris?"                                  │
│                            │                                                     │
│                            ▼                                                     │
│  2. LLM decides to call get_current_weather(location="Paris")                   │
│                            │                                                     │
│                            ▼                                                     │
│  3. AIChat reads functions.json to find tool schema                             │
│                            │                                                     │
│                            ▼                                                     │
│  4. AIChat executes: /bin/get_current_weather '{"location":"Paris"}'            │
│                            │                                                     │
│                            ▼                                                     │
│  5. Tool writes result to $LLM_OUTPUT file                                      │
│                            │                                                     │
│                            ▼                                                     │
│  6. AIChat reads $LLM_OUTPUT and returns result to LLM                          │
│                            │                                                     │
│                            ▼                                                     │
│  7. LLM generates response: "The weather in Paris is 15°C and sunny"            │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Build System

### Argcfile.sh Commands

The build system is powered by [argc](https://github.com/sigoden/argc), a Bash command framework.

| Command | Description |
|---------|-------------|
| `argc build` | Build all tools and agents |
| `argc build@tool` | Build tools only |
| `argc build@agent` | Build agents only |
| `argc check` | Verify dependencies and environment |
| `argc test` | Run all tests |
| `argc clean` | Clean build artifacts |
| `argc link-to-aichat` | Symlink to AIChat functions directory |
| `argc link-web-search <tool>` | Set web_search tool implementation |
| `argc mcp start` | Start MCP bridge server |
| `argc mcp stop` | Stop MCP bridge server |
| `argc run@tool <name> <json>` | Execute a tool manually |
| `argc run@agent <name> <action> <json>` | Execute an agent action manually |

### Build Process Details

```bash
# 1. Read tool list from tools.txt
tools=($(cat tools.txt | grep -v '^#'))

# 2. For each tool, generate JSON declaration
for tool in "${tools[@]}"; do
    # Parse comments using language-specific script
    bash scripts/build-declarations.sh "tools/$tool"
done

# 3. Combine all declarations into functions.json
jq -s '.' declarations/*.json > functions.json

# 4. Create executable symlinks in /bin/
for tool in "${tools[@]}"; do
    ln -s "../scripts/run-tool.sh" "bin/${tool%.*}"
done
```

### Declaration Generation

Each language has a declaration builder:

- `scripts/build-declarations.sh` - Parses Bash `@describe`, `@option`, `@flag` comments
- `scripts/build-declarations.js` - Parses JSDoc `@typedef`, `@property` annotations
- `scripts/build-declarations.py` - Parses Python type hints and docstrings

---

## Runtime Execution

### Environment Variables

**Injected by Runtime Scripts:**

| Variable | Description |
|----------|-------------|
| `LLM_ROOT_DIR` | Path to llm-functions directory |
| `LLM_TOOL_NAME` | Current tool name |
| `LLM_TOOL_CACHE_DIR` | Cache directory for tool |
| `LLM_AGENT_NAME` | Current agent name |
| `LLM_AGENT_FUNC` | Current agent function |
| `LLM_AGENT_ROOT_DIR` | Agent's directory |
| `LLM_AGENT_CACHE_DIR` | Cache directory for agent |

**Injected by AIChat:**

| Variable | Description |
|----------|-------------|
| `LLM_OUTPUT` | File path for tool output |
| `LLM_AGENT_VAR_<NAME>` | Agent-defined variables |

**User Configuration:**

| Variable | Description |
|----------|-------------|
| `LLM_DUMP_RESULTS` | Regex pattern for tools whose output should be printed |
| `LLM_MCP_NEED_CONFIRM` | Tools requiring user confirmation |
| `LLM_MCP_SKIP_CONFIRM` | Tools that skip confirmation |

### Runtime Script Flow (run-tool.sh)

```bash
# 1. Parse arguments
tool_name="$1"    # e.g., "execute_command"
tool_data="$2"    # e.g., '{"command": "ls -la"}'

# 2. Setup environment
export LLM_ROOT_DIR="$root_dir"
export LLM_TOOL_NAME="$tool_name"
export LLM_TOOL_CACHE_DIR="$LLM_ROOT_DIR/cache/$tool_name"

# 3. Convert JSON to command-line arguments
# {"command": "ls -la"} → --command 'ls -la'
args=$(echo "$tool_data" | jq -r 'to_args')

# 4. Create temporary output file if not provided
if [[ -z "$LLM_OUTPUT" ]]; then
    export LLM_OUTPUT="$(mktemp)"
fi

# 5. Execute tool with parsed arguments
"tools/$tool_name.sh" $args

# 6. Return output
cat "$LLM_OUTPUT"
```

### Output Handling

Tools write their output to `$LLM_OUTPUT`:

```bash
# Bash tool
echo "Result: $result" >> "$LLM_OUTPUT"

# JavaScript tool (via run-tool.js wrapper)
console.log("Result: " + result);  // Captured to LLM_OUTPUT

# Python tool
print(f"Result: {result}")  # Captured to LLM_OUTPUT
```

---

## Complete Integration Example

Here's how all components work together when a user asks AIChat about the weather:

```
User: "What's the weather in Tokyo?"
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ AIChat (aichat --role %functions%)                                  │
│                                                                     │
│ 1. Loads functions.json from AICHAT_FUNCTIONS_DIR                  │
│ 2. Sends prompt + tool definitions to LLM                          │
│ 3. LLM returns: call get_current_weather(location="Tokyo")         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ /bin/get_current_weather '{"location":"Tokyo"}'                     │
│ (symlink to scripts/run-tool.sh)                                   │
│                                                                     │
│ 1. Sets LLM_ROOT_DIR, LLM_TOOL_NAME, LLM_TOOL_CACHE_DIR           │
│ 2. Creates LLM_OUTPUT temp file                                    │
│ 3. Converts JSON to args: --location 'Tokyo'                       │
│ 4. Executes: tools/get_current_weather.sh --location 'Tokyo'       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ tools/get_current_weather.sh                                        │
│                                                                     │
│ 1. Receives $argc_location = "Tokyo"                               │
│ 2. Calls weather API                                               │
│ 3. Writes result to $LLM_OUTPUT:                                   │
│    "Tokyo: 22°C, Partly cloudy, Humidity: 65%"                     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ AIChat                                                              │
│                                                                     │
│ 1. Reads tool output from LLM_OUTPUT                               │
│ 2. Sends result back to LLM                                        │
│ 3. LLM generates: "The weather in Tokyo is 22°C and partly         │
│    cloudy with 65% humidity."                                      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
User sees: "The weather in Tokyo is 22°C and partly cloudy with 65% humidity."
```

---

## Summary

The llm-functions framework provides a modular, extensible architecture for connecting LLMs to custom tools and agents:

1. **Tool System**: Simple scripts with comment-based declarations that are automatically converted to JSON Schema
2. **Agent System**: Composable agents combining instructions, tools, and RAG documents
3. **MCP Integration**: Bidirectional - expose tools via MCP Server, import external tools via MCP Bridge
4. **AIChat Integration**: Seamless function calling through the `functions_dir` mechanism
5. **Build System**: Automated declaration generation and executable creation via argc

The framework's strength lies in its simplicity: developers write normal scripts with annotated comments, and the build system handles the rest.
