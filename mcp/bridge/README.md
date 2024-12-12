# MCP-Bridge

Let external MCP tools be used by LLM-Functions.

## Get Started

1. Create a `mpc.json` at `<llm-functions-dir>`.

```json
{
  "mcpServers": {
    "sqlite": {
      "command": "uvx",
      "args": [
        "mcp-server-sqlite",
        "--db-path",
        "/tmp/foo.db"
      ]
    },
    "github": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-github"
      ],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "<YOUR_TOKEN>"
      }
    }
  }
}
```

> MCP-Bridge will launch the server and register all the tools listed by the server. The tool identifier will be `server_toolname` to avoid clashes.

2. Run the bridge server, build mcp tool binaries, update functions.json, all with:

```
argc mcp start
```

> Run `argc mcp stop` to stop the bridge server, recover functions.json.

> Run `argc mcp logs` to check the server's logs.