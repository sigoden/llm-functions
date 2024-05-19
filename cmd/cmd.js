#!/usr/bin/env node

function loadModule() {
  const path = require("path");
  let func_name = process.argv[1];
  if (func_name.endsWith("cmd.js")) {
    func_name = process.argv[2]
  } else {
    func_name = path.basename(func_name)
  }
  if (!func_name.endsWith(".js")) {
    func_name += '.js'
  }
  const func_path = path.resolve(__dirname, `../js/${func_name}`)
  try {
    return require(func_path);
  } catch {
    console.log(`Invalid js function: ${func_name}`)
    process.exit(1)
  }
}

if (process.env["LLM_FUNCTION_DECLARATE"]) {
  const { declarate } = loadModule();
  console.log(JSON.stringify(declarate(), null, 2))
} else {
  let data = null;
  try {
    data = JSON.parse(process.env["LLM_FUNCTION_DATA"])
  } catch {
    console.log("Invalid LLM_FUNCTION_DATA")
    process.exit(1)
  }
  const { execute } = loadModule();
  execute(data)
}