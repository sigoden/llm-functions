# MCP-Server

Let LLM-functions tools/agents be used through the Model Context Protocol.

## Serve tools

```json
{
  "mcpServers": {
    "tools": {
      "command": "node",
      "args": [
        "<path-to-llm-functions>/mcp/server/index.js",
        "<path-to-llm-functions>"
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
        "<path-to-llm-functions>/mcp/server/index.js",
        "<path-to-llm-functions>",
        "<agent-name>",
      ]
    }
  }
}
```
