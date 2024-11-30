const fs = require("node:fs/promises");
const { exec } = require("node:child_process");
const { promisify } = require("node:util");
const path = require("node:path");
const { tmpdir } = require("node:os");

const jq = require("node-jq");
const jsonSchemaGenerator = require("json-schema-generator");
const input = require("@inquirer/input").default;

exports._instructions = async function () {
  const value = await input({ message: "Enter the json file path or command to generate json", required: true });
  let json_file_path, json_data;
  let generate_json_command_context = "";
  try {
    await fs.access(value);
    json_file_path = value;
    json_data = await fs.readFile(json_file_path, "utf8");
  } catch {
    generate_json_command_context = `command_to_generate_json: \`${value}\`\n`;
    const { stdout } = await promisify(exec)(value, { maxBuffer: 100 * 1024 * 1024});
    json_data = stdout;
    json_file_path = path.join(tmpdir(), `jsonviewer-${process.pid}.data.json`);
    await fs.writeFile(json_file_path, json_data);
    console.log(`ⓘ Generated json data saved to: ${json_file_path}`);
  }

  const json_schema = jsonSchemaGenerator(JSON.parse(json_data));

  return `You are a AI agent that can view and filter json data with jq.

## Context
${generate_json_command_context}json_file_path: ${json_file_path}
json_schema: ${JSON.stringify(json_schema, null, 2)}
`
}

/**
 * Print the json data.
 *
 * @typedef {Object} Args
 * @property {string} json_file_path The json file path
 * @property {string} jq_expr The jq expression
 * @param {Args} args
 */
exports.print_json = async function (args) {
  const { json_file_path, jq_expr } = args;
  const result = await jq.run(jq_expr, json_file_path, { raw: true });
  console.log(result);
}
