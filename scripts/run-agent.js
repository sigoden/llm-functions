#!/usr/bin/env node

const path = require("path");
const { createWriteStream } = require("fs");
const { readFile } = require("fs/promises");
const os = require("os");

async function main() {
  const [agentName, agentFunc, rawData] = parseArgv("run-agent.js");
  const agentData = parseRawData(rawData);

  const rootDir = path.resolve(__dirname, "..");
  await setupEnv(rootDir, agentName, agentFunc);

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
      if (envValue.startsWith('"') && envValue.endsWith('"')) {
        envValue = envValue.slice(1, -1);
      } else if (envValue.startsWith("'") && envValue.endsWith("'")) {
        envValue = envValue.slice(1, -1);
      }
      envVars.set(envName, envValue);
    }
  }

  for (const [envName, envValue] of envVars.entries()) {
    process.env[envName] = envValue;
  }
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

async function dumpResult() {
  if (!process.stdout.isTTY) {
    return;
  }
  if (!process.env["LLM_OUTPUT"]) {
    return;
  }
  let showResult = false;
  const agentName = process.env["LLM_AGENT_NAME"].toUpperCase().replace(/-/g, '_');
  const agentEnvName = `LLM_AGENT_DUMP_RESULT_${agentName}`;
  const agentEnvValue = process.env[agentEnvName] || process.env["LLM_AGENT_DUMP_RESULT"];

  const funcName = process.env["LLM_AGENT_FUNC"].toUpperCase().replace(/-/g, '_');
  const funcEnvName = `${agentEnvName}_${funcName}`;
  const funcEnvValue = process.env[funcEnvName];
  if (agentEnvValue === '1' || agentEnvValue === 'true') {
    if (funcEnvValue !== '0' && funcEnvValue !== 'false') {
      showResult = true;
    }
  } else {
    if (funcEnvValue === '1' || funcEnvValue === 'true') {
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
