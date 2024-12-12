#!/usr/bin/env node

// Usage: ./run-agent.js <agent-name> <agent-func> <agent-data>

const path = require("path");
const { readFile, writeFile } = require("fs/promises");
const os = require("os");

async function main() {
  const [agentName, agentFunc, rawData] = parseArgv("run-agent.js");
  const agentData = parseRawData(rawData);

  const rootDir = path.resolve(__dirname, "..");
  await setupEnv(rootDir, agentName, agentFunc);

  const agentToolsPath = path.resolve(rootDir, `agents/${agentName}/tools.js`);
  await run(agentName, agentToolsPath, agentFunc, agentData);
}

function parseArgv(thisFileName) {
  let agentName = process.argv[1];
  let agentFunc = "";
  let agentData = null;

  if (agentName.endsWith(thisFileName)) {
    agentName = process.argv[2];
    agentFunc = process.argv[3];
    agentData = process.argv[4];
  } else {
    agentName = path.basename(agentName);
    agentFunc = process.argv[2];
    agentData = process.argv[3];
  }

  if (agentName && agentName.endsWith(".js")) {
    agentName = agentName.slice(0, -3);
  }

  if (!agentData || !agentFunc || !agentName) {
    console.log(`Usage: ./run-agent.js <agent-name> <agent-func> <agent-data>`);
    process.exit(1);
  }

  return [agentName, agentFunc, agentData];
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

async function setupEnv(rootDir, agentName, agentFunc) {
  await loadEnv(path.resolve(rootDir, ".env"));
  process.env["LLM_ROOT_DIR"] = rootDir;
  process.env["LLM_AGENT_NAME"] = agentName;
  process.env["LLM_AGENT_FUNC"] = agentFunc;
  process.env["LLM_AGENT_ROOT_DIR"] = path.resolve(
    rootDir,
    "agents",
    agentName,
  );
  process.env["LLM_AGENT_CACHE_DIR"] = path.resolve(
    rootDir,
    "cache",
    agentName,
  );
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

async function run(agentName, agentPath, agentFunc, agentData) {
  let mod;
  if (os.platform() === "win32") {
    agentPath = `file://${agentPath}`;
  }
  try {
    mod = await import(agentPath);
  } catch {
    throw new Error(`Unable to load agent tools at '${agentPath}'`);
  }
  if (!mod || !mod[agentFunc]) {
    throw new Error(`Not module function '${agentFunc}' at '${agentPath}'`);
  }
  const value = await mod[agentFunc](agentData);
  await returnToLLM(value);
  await dumpResult(`${agentName}:${agentFunc}`);
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
  }
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

main().catch((err) => {
  console.error(err);
  process.exit(1);
});