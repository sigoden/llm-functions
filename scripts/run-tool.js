#!/usr/bin/env node

const path = require("path");
const fs = require("fs");

function parseArgv() {
  let toolName = process.argv[1];
  let toolData = null;

  if (toolName.endsWith("run-tool.js")) {
    toolName = process.argv[2];
    toolData = process.argv[3];
  } else {
    toolName = path.basename(toolName);
    toolData = process.argv[2];
  }

  if (toolName.endsWith(".js")) {
    toolName = toolName.slice(0, -3);
  }

  return [toolName, toolData];
}

function loadModule(toolName) {
  const toolFileName = `${toolName}.js`;
  const toolPath = path.resolve(
    process.env["LLM_ROOT_DIR"],
    `tools/${toolFileName}`,
  );
  try {
    return require(toolPath);
  } catch {
    console.log(`Invalid tooltion: ${toolFileName}`);
    process.exit(1);
  }
}

function loadEnv(filePath) {
  try {
    const data = fs.readFileSync(filePath, "utf-8");
    const lines = data.split("\n");

    lines.forEach((line) => {
      if (line.trim().startsWith("#") || line.trim() === "") return;

      const [key, ...value] = line.split("=");
      process.env[key.trim()] = value.join("=").trim();
    });
  } catch {}
}

const LLM_ROOT_DIR = path.resolve(__dirname, "..");
process.env["LLM_ROOT_DIR"] = LLM_ROOT_DIR;

loadEnv(path.resolve(LLM_ROOT_DIR, ".env"));

const [toolName, toolData] = parseArgv();

process.env["LLM_TOOL_NAME"] = toolName;
process.env["LLM_TOOL_CACHE_DIR"] = path.resolve(
  LLM_ROOT_DIR,
  "cache",
  toolName,
);

if (!toolData) {
  console.log("No json data");
  process.exit(1);
}

let data = null;
try {
  data = JSON.parse(toolData);
} catch {
  console.log("Invalid json data");
  process.exit(1);
}

const { run } = loadModule(toolName);
run(data);
