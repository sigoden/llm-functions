#!/usr/bin/env node

const path = require("path");
const { createWriteStream } = require("fs");
const { readFile } = require("fs/promises");
const os = require("os");

async function main() {
  const [toolName, rawData] = parseArgv("run-tool.js");
  const toolData = parseRawData(rawData);

  const rootDir = path.resolve(__dirname, "..");
  await setupEnv(rootDir, toolName);

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

  if (toolName && toolName.endsWith(".js")) {
    toolName = toolName.slice(0, -3);
  }

  if (!toolData || !toolName) {
    console.log(`Usage: ./run-tools.js <tool-name> <tool-data>`);
    process.exit(1);
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

async function setupEnv(rootDir, toolName) {
  await loadEnv(path.resolve(rootDir, ".env"));
  process.env["LLM_ROOT_DIR"] = rootDir;
  process.env["LLM_TOOL_NAME"] = toolName;
  process.env["LLM_TOOL_CACHE_DIR"] = path.resolve(rootDir, "cache", toolName);
}

async function loadEnv(filePath) {
  let lines = [];
  try {
    const data = await readFile(filePath, "utf-8");
    lines = data.split("\n");
  } catch {
    return;
  }

  const envVars = new Map();

  for (const line of lines) {
    if (line.trim().startsWith("#") || line.trim() === "") {
      continue;
    }

    const [key, ...valueParts] = line.split("=");
    const envName = key.trim();

    if (!process.env[envName]) {
      let envValue = valueParts.join("=").trim();
      if ((envValue.startsWith('"') && envValue.endsWith('"')) || (envValue.startsWith("'") && envValue.endsWith("'"))) {
        envValue = envValue.slice(1, -1);
      }
      envVars.set(envName, envValue);
    }
  }

  for (const [envName, envValue] of envVars.entries()) {
    process.env[envName] = envValue;
  }
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
  await dumpResult();
}

function returnToLLM(value) {
  if (value === null || value === undefined) {
    return;
  }
  let writer = process.stdout;
  if (process.env["LLM_OUTPUT"]) {
    writer = createWriteStream(process.env["LLM_OUTPUT"]);
  }
  const type = typeof value;
  if (type === "string" || type === "number" || type === "boolean") {
    writer.write(value.toString());
  } else if (type === "object") {
    const proto = Object.prototype.toString.call(value);
    if (proto === "[object Object]" || proto === "[object Array]") {
      const valueStr = JSON.stringify(value, null, 2);
      require("assert").deepStrictEqual(value, JSON.parse(valueStr));
      writer.write(valueStr);
    }
  }
}

async function dumpResult() {
  if (!process.stdout.isTTY) {
    return;
  }
  if (!process.env["LLM_OUTPUT"]) {
    return;
  }
  let showResult = false;
  const toolName = process.env["LLM_TOOL_NAME"].toUpperCase().replace(/-/g, '_');
  const envName = `LLM_TOOL_DUMP_RESULT_${toolName}`;
  const envValue = process.env[envName];
  if (process.env.LLM_TOOL_DUMP_RESULT === '1' || process.env.LLM_TOOL_DUMP_RESULT === 'true') {
    if (envValue !== '0' && envValue !== 'false') {
      showResult = true;
    }
  } else {
    if (envValue === '1' || envValue === 'true') {
      showResult = true;
    }
  }
  if (!showResult) {
    return;
  }

  let data = "";
  try {
    data = await readFile(process.env["LLM_OUTPUT"], "utf-8");
  } catch {
    return;
  }
  process.stdout.write(`\x1b[2m----------------------\n${data}\n----------------------\x1b[0m\n`);
}

(async () => {
  try {
    await main();
  } catch (err) {
    console.error(err?.message || err);
    process.exit(1);
  }
})();
