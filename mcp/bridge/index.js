#!/usr/bin/env node

import * as path from "node:path";
import * as fs from "node:fs";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import express from "express";

const app = express();
const PORT = process.env.MCP_BRIDGE_PORT || 8808;

let [rootDir] = process.argv.slice(2);

if (!rootDir) {
  console.error("Usage: mcp-bridge <llm-functions-dir>");
  process.exit(1);
}

let mcpServers = {};
const mcpJsonPath = path.join(rootDir, "mcp.json");
try {
  const data = await fs.promises.readFile(mcpJsonPath, "utf8");
  mcpServers = JSON.parse(data)?.mcpServers;
} catch {
  console.error(`Failed to read json at '${mcpJsonPath}'`);
  process.exit(1);
}

async function startMcpServer(id, serverConfig) {
  console.log(`Starting ${id} server...`);
  const capabilities = { tools: {} }
  const transport = new StdioClientTransport({
    ...serverConfig,
    stderr: "inherit",
  });
  const client = new Client(
    { name: id, version: "1.0.0" },
    { capabilities }
  );
  try {
    await client.connect(transport)
  } catch (err) {
    console.error(`Failed to connect to ${id} client: ${err}`);
  }
  const { tools: toolDefinitions } = await client.listTools()
  const tools = toolDefinitions.map(
    ({ name, description, inputSchema }) =>
    ({
      spec: {
        name: `${normalizeToolName(`${id}_${name}`)}`,
        description,
        parameters: inputSchema,
      },
      impl: async (args) => {
        const res = await client.callTool({
          name: name,
          arguments: args,
        });
        const content = res.content;
        let text = arrayify(content)?.map((c) => {
          switch (c.type) {
            case "text":
              return c.text || ""
            case "image":
              return c.data
            case "resource":
              return c.resource?.uri || ""
            default:
              return c
          }
        })
          .join("\n");
        if (res.isError) {
          text = `Tool Error\n${text}`;
        }
        return text;
      },
    })
  );
  return {
    tools,
    [Symbol.asyncDispose]: async () => {
      console.log(`Closing ${id}`)
      await client.close()
      await transport.close()
    },
  }
}

async function runBridge() {
  const runningMcpServers = await Promise.all(
    Object.entries(mcpServers).map(
      async ([name, serverConfig]) =>
        await startMcpServer(name, serverConfig)
    )
  );
  const stopMcpServers = () => Promise.all(runningMcpServers.map((s) => s[Symbol.asyncDispose]()));
  const definitions = runningMcpServers.flatMap((s) => s.tools.map(t => t.spec));
  const runTool = async (name, args) => {
    for (const server of runningMcpServers) {
      const tool = server.tools.find((t) => t.spec.name === name);
      if (tool) {
        return tool.impl(args);
      }
    }
    return `Not found tool '${name}'`;
  };

  app.use(express.json());

  app.get("/", (_req, res) => {
    res.send(`# MCP Bridge API
  
- POST /tools/:name 
  \`\`\`
  curl -X POST http://localhost:8808/tools/filesystem_write_file  \\
    -H 'content-type: application/json' \\
    -d '{"path": "/tmp/file1", "content": "hello world"}'
  \`\`\`
- GET /tools
  \`\`\`
  curl http://localhost:8808/tools
  \`\`\`
- GET /health
  \`\`\`
  curl http://localhost:8808/health # print \`OK\`
  \`\`\`
  `);
  });

  app.get("/tools", (_req, res) => {
    res.json(definitions);
  });

  app.post("/tools/:name", async (req, res) => {
    try {
      const output = await runTool(req.params.name, req.body);
      res.send(output);
    } catch (err) {
      res.status(500).send(err);
    }
  });

  app.get("/health", (_req, res) => {
    res.send("OK");
  });

  const server = app.listen(PORT, () => {
    console.log(`Server is running on port ${PORT}`);
  });

  return () => {
    server.close(async () => {
      await stopMcpServers();
      process.exit(0);
    })
  };
}

function arrayify(a) {
  let r;
  if (a === undefined) r = [];
  else if (Array.isArray(a)) r = a.slice(0);
  else r = [a];

  return r
}

function normalizeToolName(name) {
  return name.toLowerCase().replace(/-/g, "_");
}

runBridge()
  .then(stop => {
    process.on('SIGINT', stop);
    process.on('SIGTERM', stop);
  })
  .catch(console.error);