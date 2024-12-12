# MCP-Server

Let LLM-functions tools/agents be used through the Model Context Protocol.

## Serve tools

```json
{
  "mcpServers": {
    "tools": {
      "command": "npx",
      "args": [
        "mcp-llm-functions",
        "<llm-functions-dir>"
      ]
    }
  }
}
```

## Serve the agent

```json
{
  "mcpServers": {
    "<agent-name>": {
      "command": "node",
      "args": [
        "mcp-llm-functions",
        "<llm-functions-dir>"
        "<agent-name>",
      ]
    }
  }
}
```

## Environment Variables

- `AGENT_TOOLS_ONLY`: Set to `true` or `1` to ignore shared tools and display only agent tools.