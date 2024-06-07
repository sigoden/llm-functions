#!/usr/bin/env node

const path = require("path");
const fs = require("fs");

function parseArgv() {
  let funcName = process.argv[1];
  let funcData = null;

  if (funcName.endsWith("run-tool.js")) {
    funcName = process.argv[2];
    funcData = process.argv[3];
  } else {
    funcName = path.basename(funcName);
    funcData = process.argv[2];
  }

  if (funcName.endsWith(".js")) {
    funcName = funcName.slice(0, -3);
  }

  return [funcName, funcData];
}

function loadFunc(funcName) {
  const funcFileName = `${funcName}.js`;
  const funcPath = path.resolve(
    process.env["LLM_FUNCTIONS_DIR"],
    `tools/${funcFileName}`,
  );
  try {
    return require(funcPath);
  } catch {
    console.log(`Invalid function: ${funcFileName}`);
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

process.env["LLM_FUNCTIONS_DIR"] = path.resolve(__dirname, "..");

loadEnv(path.resolve(process.env["LLM_FUNCTIONS_DIR"], ".env"));

const [funcName, funcData] = parseArgv();

process.env["LLM_FUNCTION_NAME"] = funcName;

if (!funcData) {
  console.log("No json data");
  process.exit(1);
}

let args;
try {
  args = JSON.parse(funcData);
} catch {
  console.log("Invalid json data");
  process.exit(1);
}

const { run } = loadFunc(funcName);
run(args);
