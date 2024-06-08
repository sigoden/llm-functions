#!/usr/bin/env node

const path = require("path");
const fs = require("fs");
const os = require("os");

async function main() {
  const [botName, botFunc, rawData] = parseArgv("run-bot.js");
  const botData = parseRawData(rawData);

  const rootDir = path.resolve(__dirname, "..");
  setupEnv(rootDir, botName);

  const botToolsPath = path.resolve(rootDir, `bots/${botName}/tools.js`);
  await run(botToolsPath, botFunc, botData);
}

function parseArgv(thisFileName) {
  let botName = process.argv[1];
  let botFunc = "";
  let botData = null;

  if (botName.endsWith(thisFileName)) {
    botName = process.argv[2];
    botFunc = process.argv[3];
    botData = process.argv[4];
  } else {
    botName = path.basename(botName);
    botFunc = process.argv[2];
    botData = process.argv[3];
  }

  if (botName.endsWith(".js")) {
    botName = botName.slice(0, -3);
  }

  return [botName, botFunc, botData];
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

function setupEnv(rootDir, botName) {
  process.env["LLM_ROOT_DIR"] = rootDir;
  loadEnv(path.resolve(rootDir, ".env"));
  process.env["LLM_BOT_NAME"] = botName;
  process.env["LLM_BOT_ROOT_DIR"] = path.resolve(rootDir, "bots", botName);
  process.env["LLM_BOT_CACHE_DIR"] = path.resolve(rootDir, "cache", botName);
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

async function run(botPath, botFunc, botData) {
  let mod;
  if (os.platform() === "win32") {
    botPath = `file://${botPath}`;
  }
  try {
    mod = await import(botPath);
  } catch {
    throw new Error(`Unable to load bot tools at '${botPath}'`);
  }
  if (!mod || !mod[botFunc]) {
    throw new Error(`Not module function '${botFunc}' at '${botPath}'`);
  }
  const value = await mod[botFunc](botData);
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
