#!/usr/bin/env node

const path = require("path");
const fs = require("fs");
const os = require("os");

async function main() {
  const [agentName, agentFunc, rawData] = parseArgv("run-agent.js");
  const agentData = parseRawData(rawData);

  const rootDir = path.resolve(__dirname, "..");
  setupEnv(rootDir, agentName);

  const agentToolsPath = path.resolve(rootDir, `agents/${agentName}/tools.js`);
  await run(agentToolsPath, agentFunc, agentData);
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

  if (agentName.endsWith(".js")) {
    agentName = agentName.slice(0, -3);
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

function setupEnv(rootDir, agentName) {
  process.env["LLM_ROOT_DIR"] = rootDir;
  loadEnv(path.resolve(rootDir, ".env"));
  process.env["LLM_AGENT_NAME"] = agentName;
  process.env["LLM_AGENT_ROOT_DIR"] = path.resolve(rootDir, "agents", agentName);
  process.env["LLM_AGENT_CACHE_DIR"] = path.resolve(rootDir, "cache", agentName);
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

async function run(agentPath, agentFunc, agentData) {
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
  dumpValue(value);
}

function dumpValue(value) {
  if (value === null || value === undefined) {
    return;
  }
  const type = typeof value;
  if (type === "string" || type === "number" || type === "boolean") {
    console.log(value);
  } else if (type === "object") {
    const proto = Object.prototype.toString.call(value);
    if (proto === "[object Object]" || proto === "[object Array]") {
      const valueStr = JSON.stringify(value, null, 2);
      require("assert").deepStrictEqual(value, JSON.parse(valueStr));
      console.log(valueStr);
    }
  }
}

main();
