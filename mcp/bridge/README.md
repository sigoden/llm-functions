# MCP-Bridge

Let external MCP tools be used by LLM-Functions.

## Get Started

### 1. Create a `mcp.json` at `<llm-functions-dir>`.

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
    "git": {
      "command": "uvx",
      "args": [
        "mcp-server-git",
        "--repository",
        "path/to/git/repo"
      ],
      "prefix": false
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

> MCP-Bridge will launch the server and register all the tools listed by the server. 

> To avoid name clashes, The server automatically prefix tool names with `<server>_`. You can disable this behavior by add `prefix: false` to server configuration.

### 2. Run the bridge server, build mcp tool binaries, update functions.json, all with:

```
argc mcp start
```

> Run `argc mcp stop` to stop the bridge server, recover functions.json.

> Run `argc mcp logs` to check the server's logs.

## Web search and page fetching with Parallel

[Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp) provides free web search and page fetching without an account or API key. Anonymous searches use Fast mode and are rate limited.

The bridge launches stdio servers. Use [mcp-remote](https://github.com/punkpeye/mcp-remote) to connect it to Parallel's Streamable HTTP endpoint. Install Node.js 22 or newer, with `npx` available on your PATH, in addition to the repository's prerequisites.

Add the `parallel` entry to the `mcpServers` object in your `mcp.json`, keeping any existing servers:

```json
{
  "mcpServers": {
    "parallel": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote@0.15.1",
        "https://search.parallel.ai/mcp",
        "--transport",
        "http-only",
        "--header",
        "User-Agent:llm-functions"
      ]
    }
  }
}
```

From the repository root, start or restart the bridge:

```sh
argc mcp start
```

This downloads `mcp-remote` on first use and registers `parallel_web_search` and `parallel_web_fetch` with the default tool-name prefix. These tools are added alongside your existing tools; your selected `web_search.sh` provider stays unchanged.

Try a search, then fetch a page through the bridge:

```sh
argc mcp run@tool parallel_web_search '{"objective":"Find the official AIChat documentation.","search_queries":["AIChat official documentation sigoden"]}'
argc mcp run@tool parallel_web_fetch '{"urls":["https://github.com/sigoden/aichat"],"objective":"Find how to enable function calling."}'
```

The tools are also available through AIChat's `%functions%` role after [linking LLM-Functions to AIChat](../../README.md#3-link-llm-functions-and-aichat). Use `argc mcp logs` to troubleshoot a failed connection.
