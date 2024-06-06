#!/usr/bin/env node

const path = require("path");
const fs = require('fs');

function parseArgv() {
  let func_file = process.argv[1];
  let func_data = null;

  if (func_file.endsWith("bin.js")) {
    func_file = process.argv[2]
    func_data = process.argv[3]
  } else {
    func_file = path.basename(func_file)
    func_data = process.argv[2];
  }

  if (!func_file.endsWith(".js")) {
    func_file += '.js'
  }

  return [func_file, func_data]
}

function loadFunc(func_file) {
  const func_path = path.resolve(process.env["LLM_FUNCTIONS_DIR"], `tools/${func_file}`)
  try {
    return require(func_path);
  } catch {
    console.log(`Invalid function: ${func_file}`)
    process.exit(1)
  }
}

function loadEnv(filePath) {
  try {
    const data = fs.readFileSync(filePath, 'utf-8');
    const lines = data.split('\n');

    lines.forEach(line => {
      if (line.trim().startsWith('#') || line.trim() === '') return;

      const [key, ...value] = line.split('=');
      process.env[key.trim()] = value.join('=').trim();
    });
  } catch {}
}

process.env["LLM_FUNCTIONS_DIR"] = path.resolve(__dirname, "..");

loadEnv(path.resolve(process.env["LLM_FUNCTIONS_DIR"], ".env"));

const [func_file, func_data] = parseArgv();

if (process.env["LLM_FUNCTION_ACTION"] == "declarate") {
  const { declarate } = loadFunc(func_file);
  console.log(JSON.stringify(declarate(), null, 2))
} else {
  if (!func_data) {
    console.log("No json data");
    process.exit(1)
  }

  let args;
  try {
    args = JSON.parse(func_data)
  } catch {
    console.log("Invalid json data")
    process.exit(1)
  }

  const { execute } = loadFunc(func_file);
  execute(args)
}