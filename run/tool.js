#!/usr/bin/env node

const path = require("path");

function parseArgv() {
  let func_file = process.argv[1];
  let func_data = null;

  if (func_file.endsWith("tool.js")) {
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
  const func_path = path.resolve(__dirname, `../tools/js/${func_file}`)
  try {
    return require(func_path);
  } catch {
    console.log(`Invalid function: ${func_file}`)
    process.exit(1)
  }
}

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