#!/usr/bin/env node

// Usage: ./run-tool.js <tool-name> <tool-data>

const path = require("path");
const { readFile, writeFile } = require("fs/promises");
const os = require("os");

// Standard exit codes
const EXIT_SUCCESS = 0;
const EXIT_GENERAL_ERROR = 1;
const EXIT_INVALID_INPUT = 2;
const EXIT_FILE_NOT_FOUND = 3;
const EXIT_PERMISSION_DENIED = 4;
const EXIT_NETWORK_ERROR = 5;
const EXIT_TIMEOUT = 124;
const EXIT_COMMAND_NOT_FOUND = 127;

async function main() {
  try {
    const [toolName, rawData] = parseArgv("run-tool.js");
    const toolData = parseRawData(rawData);

    const rootDir = path.resolve(__dirname, "..");
    await setupEnv(rootDir, toolName);

    const toolPath = path.resolve(rootDir, `tools/${toolName}.js`);
    await run(toolName, toolPath, "run", toolData);
  } catch (err) {
    console.error(err.message);
    process.exit(err.code || EXIT_GENERAL_ERROR);
  }
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
    const err = new Error(`Usage: ./run-tool.js <tool-name> <tool-data>`);
    err.code = EXIT_INVALID_INPUT;
    throw err;
  }

  return [toolName, toolData];
}

function parseRawData(data) {
  if (!data) {
    const err = new Error("No JSON data");
    err.code = EXIT_INVALID_INPUT;
    throw err;
  }
  try {
    return JSON.parse(data);
  } catch {
    const err = new Error("Invalid JSON data");
    err.code = EXIT_INVALID_INPUT;
    throw err;
  }
}

async function setupEnv(rootDir, toolName) {
  await loadEnv(path.resolve(rootDir, ".env"));
  process.env["LLM_ROOT_DIR"] = rootDir;
  process.env["LLM_TOOL_NAME"] = toolName;
  process.env["LLM_TOOL_CACHE_DIR"] = path.resolve(rootDir, "cache", toolName);
  if (process.stdout.isTTY) {
    process.env["LLM_OUTPUT_COLOR"] = "1";
  }
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

async function run(toolName, toolPath, toolFunc, toolData) {
  if (os.platform() === "win32") {
    toolPath = `file://${toolPath}`;
  }
  
  let mod;
  try {
    mod = await import(toolPath);
  } catch (err) {
    const error = new Error(`Failed to import tool module: ${err.message}`);
    error.code = EXIT_FILE_NOT_FOUND;
    throw error;
  }
  
  if (!mod || !mod[toolFunc]) {
    const error = new Error(`Not module function '${toolFunc}' at '${toolPath}'`);
    error.code = EXIT_GENERAL_ERROR;
    throw error;
  }
  
  let value;
  try {
    value = await mod[toolFunc](toolData);
  } catch (err) {
    // Propagate the tool's exit code if available
    if (err.code) {
      process.exit(err.code);
    }
    throw err;
  }
  
  await returnToLLM(value);
  await dumpResult(toolName);
}

async function returnToLLM(value) {
  if (value === null || value === undefined) {
    return;
  }
  const write = async (value) => {
    if (process.env["LLM_OUTPUT"]) {
      await writeFile(process.env["LLM_OUTPUT"], value);
    } else {
      process.stdout.write(value);
    }
  };
  const type = typeof value;
  if (type === "string" || type === "number" || type === "boolean") {
    await write(value.toString());
  } else if (type === "object") {
    const proto = Object.prototype.toString.call(value);
    if (proto === "[object Object]" || proto === "[object Array]") {
      const valueStr = JSON.stringify(value, null, 2);
      require("assert").deepStrictEqual(value, JSON.parse(valueStr));
      await write(valueStr);
    }
  }
}

async function dumpResult(name) {
  if (!process.env["LLM_DUMP_RESULTS"] || !process.env["LLM_OUTPUT"] || !process.stdout.isTTY) {
    return;
  }
  let showResult = false;
  try {
    if (new RegExp(`\\b(${process.env["LLM_DUMP_RESULTS"]})\\b`).test(name)) {
      showResult = true;
    }
  } catch { }

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

main();
