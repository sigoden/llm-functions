#!/usr/bin/env node

const path = require("path");
const fs = require("fs");
const os = require("os");

async function main() {
  const [toolName, rawData] = parseArgv("run-tool.js");
  const toolData = parseRawData(rawData);

  const rootDir = path.resolve(__dirname, "..");
  setupEnv(rootDir, toolName);

  const toolPath = path.resolve(rootDir, `tools/${toolName}.js`);
  await run(toolPath, "run", toolData);
}

function parseArgv(thisFileName) {
  let toolName = process.argv[1];
  let toolData = null;

  if (toolName.endsWith(thisFileName)) {
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

function parseRawData(data) {
  if (!data) {
    throw new Error("No JSON data");
  }
  try {
    return JSON.parse(data);
  } catch {
    throw new Error("Invalid JSON data");
  }
}

function setupEnv(rootDir, toolName) {
  loadEnv(path.resolve(rootDir, ".env"));
  process.env["LLM_ROOT_DIR"] = rootDir;
  process.env["LLM_TOOL_NAME"] = toolName;
  process.env["LLM_TOOL_CACHE_DIR"] = path.resolve(rootDir, "cache", toolName);
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

async function run(toolPath, toolFunc, toolData) {
  let mod;
  if (os.platform() === "win32") {
    toolPath = `file://${toolPath}`;
  }
  try {
    mod = await import(toolPath);
  } catch {
    throw new Error(`Unable to load tool at '${toolPath}'`);
  }
  if (!mod || !mod[toolFunc]) {
    throw new Error(`Not module function '${toolFunc}' at '${toolPath}'`);
  }
  const value = await mod[toolFunc](toolData);
  returnToLLM(value);
}

function returnToLLM(value) {
  if (value === null || value === undefined) {
    return;
  }
  let writer = process.stdout;
  if (process.env["LLM_OUTPUT"]) {
    writer = fs.createWriteStream(process.env["LLM_OUTPUT"]);
  }
  const type = typeof value;
  if (type === "string" || type === "number" || type === "boolean") {
    writer.write(value);
  } else if (type === "object") {
    const proto = Object.prototype.toString.call(value);
    if (proto === "[object Object]" || proto === "[object Array]") {
      const valueStr = JSON.stringify(value, null, 2);
      require("assert").deepStrictEqual(value, JSON.parse(valueStr));
      writer.write(valueStr);
    }
  }
}

(async () => {
  try {
    await main();
  } catch (err) {
    console.error(err?.message || err);
    process.exit(1);
  }
})();
