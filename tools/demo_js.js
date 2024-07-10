/**
 * Demonstrate how to create a tool using Javascript and how to use comments.
 * @typedef {Object} Args
 * @property {string} string - Define a required string property
 * @property {'foo'|'bar'} string_enum - Define a required string property with enum
 * @property {string} [string_optional] - Define a optional string property
 * @property {boolean} boolean - Define a required boolean property
 * @property {Integer} integer - Define a required integer property
 * @property {number} number - Define a required number property
 * @property {string[]} array - Define a required string array property
 * @property {string[]} [array_optional] - Define a optional string array property
 * @param {Args} args
 */
exports.run = function run(args) {
  let output = `string: ${args.string}
string_enum: ${args.string_enum}
string_optional: ${args.string_optional}
boolean: ${args.boolean}
integer: ${args.integer}
number: ${args.number}
array: ${args.array}
array_optional: ${args.array_optional}`;
  for (const [key, value] of Object.entries(process.env)) {
    if (key.startsWith("LLM_")) {
      output = `${output}\n${key}: ${value}`;
    }
  }
  return output;
}
