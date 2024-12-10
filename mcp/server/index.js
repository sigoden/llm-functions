#!/usr/bin/env node

import * as path from "node:path";
import * as fs from "node:fs";
import * as os from "node:os";
import { v4 as uuid } from "uuid";
import { spawn } from "node:child_process";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ErrorCode,
  ListToolsRequestSchema,
  McpError,
} from "@modelcontextprotocol/sdk/types.js";

let [rootDir, agentName] = process.argv.slice(2);
if (!rootDir) {
  console.error("Usage: mcp-llm-functions <llm-functions-dir> [<agent-name>]");
  process.exit(1);
}
rootDir = path.resolve(rootDir);

let functionsJsonPath = path.join(rootDir, "functions.json");
if (agentName) {
  functionsJsonPath = path.join(rootDir, "agents", agentName, "functions.json");
}
let functions = [];
try {
  const data = await fs.promises.readFile(functionsJsonPath, "utf8");
  functions = JSON.parse(data);
} catch {
  console.error(`Failed to read functions at '${functionsJsonPath}'`);
  process.exit(1);
}
const env = Object.assign({}, process.env, {
  PATH: `${path.join(rootDir, "bin")}:${process.env.PATH}`
});

const server = new Server(
  {
    name: `llm-functions/${agentName || "common-tools"}`,
    version: "0.1.0",
  },
  {
    capabilities: {
      tools: {},
    },
  },
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: functions.map((f) => ({
      name: f.name,
      description: f.description,
      inputSchema: f.parameters,
    })),
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const functionObj = functions.find((f) => f.name === request.params.name);
  if (!functionObj) {
    throw new McpError(ErrorCode.InvalidRequest, `Unexpected tool '${request.params.name}'`);
  }
  let command = request.params.name;
  let args = [JSON.stringify(request.params.arguments || {})];
  if (agentName && functionObj.agent) {
    args.unshift(command);
    command = agentName;
  }
  const tmpFile = path.join(os.tmpdir(), `mcp-llm-functions-${process.pid}-eval-${uuid()}`);
  const { exitCode, stderr } = await runCommand(command, args, { ...env, LLM_OUTPUT: tmpFile });
  if (exitCode === 0) {
    let output = '';
    try {
      output = await fs.promises.readFile(tmpFile, "utf8");
    } catch { };
    return {
      content: [{ type: "text", value: output }],
    }
  } else {
    return {
      isError: true,
      error: stderr,
    };
  }
});

function runCommand(command, args, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: ['ignore', 'ignore', 'pipe'],
      env,
    });

    let stderr = '';

    child.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    child.on('close', (exitCode) => {
      resolve({ exitCode, stderr });
    });

    child.on('error', (err) => {
      reject(err);
    });
  });
}

async function runServer() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("LLM-Functions MCP Server running on stdio");
}

runServer().catch(console.error);