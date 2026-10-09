#!/usr/bin/env node
// @describe Generate JSON declarations for tool/agent functions from script files.
//
// Parses JSDoc comments and function signatures to create a JSON array
// describing the available tools/functions, their descriptions, and parameters.
// This script is designed to be run from the command line.
//
// Usage: ./build-declarations.js <script-file>
// Example: ./build-declarations.js tools/fs_ls.js

const fs = require("fs");
const path = require("path");

// The expected entry function name for tool scripts (e.g., 'run' in Node.js tools).
const TOOL_ENTRY_FUNC = "run";

/**
 * Main function to orchestrate the script execution.
 * Parses command-line arguments, reads the script file, extracts function declarations,
 * and outputs the declarations in JSON format.
 */
async function main() {
  // Validate command-line arguments: Ensure a script file path is provided.
  if (process.argv.length < 3) {
    console.error("Usage: ./build-declarations.js <script-file>");
    process.exit(1); // Exit with an error code if usage is incorrect.
  }

  const scriptfile = process.argv[2]; // The path to the script file to process.
  let contents;

  try {
    // Read the script file content safely, handling potential file errors.
    contents = fs.readFileSync(scriptfile, "utf8");
  } catch (err) {
    console.error(`Error reading file ${scriptfile}: ${err.message}`);
    process.exit(1); // Exit if the file cannot be read.
  }

  // Determine if the script is a tool based on its directory. Tools are expected in the 'tools/' directory.
  const isTool = scriptfile.split(path.sep).includes("tools");
  let functions;

  try {
    // Extract function names and their associated JSDoc comments from the script content.
    functions = extractFunctions(contents, isTool);
  } catch (err) {
    console.error(`Error during function extraction for ${scriptfile}: ${err.message}`);
    process.exit(1); // Exit if function extraction fails.
  }

  let declarations = [];
  // Iterate over each extracted function to build its declaration object.
  for (const { funcName, jsdoc } of functions) {
    try {
      // Parse the JSDoc comment to get the function's description and parameters.
      const { description, params } = parseJsDoc(jsdoc, funcName);
      // Skip functions that do not have a description (as per requirement).
      if (!description) continue;
      // Build the JSON declaration object for the function.
      const declaration = buildDeclaration(funcName, description, params);
      declarations.push(declaration);
    } catch (err) {
      // Log errors encountered during parsing of a specific function but continue processing others.
      console.error(`Error processing function '${funcName}' in ${scriptfile}: ${err.message}`);
      // Depending on requirements, one might choose to exit here: process.exit(1);
    }
  }

  // Special handling for tool scripts:
  // If it's a tool and we found declarations, ensure the first declaration's name
  // matches the base filename (without extension), as tools typically have a single entry point.
  if (isTool && declarations.length > 0) {
    declarations[0].name = getBasename(scriptfile);
  }

  // Output the generated declarations as a JSON array, pretty-printed for readability.
  console.log(JSON.stringify(declarations, null, 2));
}

/**
 * Extracts function names and their associated JSDoc comments from the script's content.
 * It differentiates between regular module exports and dedicated tool entry points.
 * @param {string} contents - The full content of the script file.
 * @param {boolean} isTool - A flag indicating if the script is a tool (determines parsing strategy).
 * @returns {{funcName: string, jsdoc: string}[]} An array of objects, each containing a function name and its JSDoc string.
 */
function extractFunctions(contents, isTool) {
  const output = []; // Array to store extracted function information.
  const lines = contents.split("\n"); // Split content into lines for line-by-line processing.
  let jsdoc = ""; // Accumulator for the current JSDoc comment block.
  let isInComment = false; // Flag to track if we are currently inside a JSDoc comment.

  for (let line of lines) {
    // Detect the start of a JSDoc comment block (/**).
    if (/^\s*\/\*\*/.test(line)) {
      isInComment = true;
      jsdoc = line; // Initialize jsdoc accumulator with the starting line.
      continue; // Move to the next line.
    }

    // If we are currently inside a JSDoc comment block.
    if (isInComment) {
      jsdoc += `\n${line}`; // Append the current line to the jsdoc accumulator.
      // Detect the end of a JSDoc comment block (*/).
      if (/^\s*\*\//.test(line)) {
        isInComment = false; // Exit the comment block.
        // The associated function definition will be processed in the next iteration's 'else' block.
        continue; // Move to the next line.
      }
    } else {
      // We are in a code block (not a comment).
      // Process only if we have accumulated JSDoc and the current line is not empty after trimming.
      if (jsdoc && line.trim() !== "") {
        let funcName = null; // Variable to store the detected function name.
        let match = null; // To store regex match results.

        if (isTool) {
          // For tool scripts, specifically look for the designated entry function (e.g., 'run').
          // This regex checks for 'export function run', 'export async function run', or 'exports.run = ...'
          if (new RegExp(`^export (async )?function ${TOOL_ENTRY_FUNC}|^exports\.${TOOL_ENTRY_FUNC}`).test(line)) {
            funcName = TOOL_ENTRY_FUNC;
          }
        } else {
          // For regular module scripts, look for exported functions using common patterns.
          // Pattern: 'export function funcName' or 'export async function funcName'.
          match = /^export (async )?function ([A-Za-z0-9_]+)/.exec(line);
          if (match) {
            funcName = match[2]; // Extract function name from capture group 2.
          } else {
            // Pattern: 'exports.funcName = function' or 'exports.funcName = async function'.
            match = /^exports\.([A-Za-z0-9_]+) = (async )?function /.exec(line);
            if (match) {
              funcName = match[1]; // Extract function name from capture group 1.
            }
          }
        }

        // If a function name was found and it's not a private function (starts with '_').
        if (funcName && !funcName.startsWith("_")) {
          // Store the function name and its associated JSDoc. Trim JSDoc to remove leading/trailing whitespace.
          output.push({ funcName, jsdoc: jsdoc.trim() });
        }
      }
      // Reset the jsdoc accumulator after processing the line associated with the JSDoc.
      jsdoc = "";
    }
  }
  return output; // Return the array of extracted function info.
}

/**
 * Parses a JSDoc comment string to extract the main description and parameter details.
 * Handles various JSDoc tags like @property, and determines parameter properties like name, type, and required status.
 * @param {string} jsdoc - The JSDoc comment string associated with a function.
 * @param {string} funcName - The name of the function being parsed (used for error reporting context).
 * @returns {{description: string, params: Param[]}} An object containing the extracted description (string) and an array of parsed parameters (Param[]).
 * @throws {Error} If JSDoc parsing encounters critical issues.
 */
function parseJsDoc(jsdoc, funcName) {
  const lines = jsdoc.split("\n"); // Split JSDoc into lines.
  let description = ""; // Accumulator for the main function description.
  const rawParams = []; // Stores raw descriptions of parameters (usually from @property tags).
  let currentTag = ""; // Tracks the current JSDoc tag being processed (e.g., 'param', 'property', 'returns').

  for (let line of lines) {
    // Clean up each line: remove leading JSDoc comment syntax (/**, */, *) and trim whitespace.
    line = line.replace(/^\s*(\/\*\*|\*\/|\*)\s*/, "").trim();

    // Detect if the line starts with a JSDoc tag (e.g., @tag).
    const tagMatch = /^@(\w+)/.exec(line);
    if (tagMatch) {
      currentTag = tagMatch[1]; // Update the current tag.
      line = line.slice(tagMatch[0].length).trim(); // Remove the tag itself from the line to process the rest.
    }

    if (!currentTag || currentTag === "describe") {
      // If no current tag is active or it is @describe, this line is part of the main function description.
      if (line) description += ` ${line}`; // Append to description, adding a space for multi-line descriptions.
    } else if (currentTag === "property" || currentTag === "param") {
      // Special handling for @property or @param tags, often used for object parameters.
      // Supports multi-line tags by appending to the previous entry if it's not a new tag line.
      if (rawParams.length > 0 && !tagMatch) {
        // Append to the last raw parameter entry if the current line continues it.
        rawParams[rawParams.length - 1] += ` ${line}`;
      } else if (line) {
        // Add a new entry if the line contains content.
        rawParams.push(line);
      }
    }
    // If we were processing a tag and the current line becomes empty after tag processing,
    // or if the tag was just detected and the remainder of the line is empty, reset currentTag.
    // This ensures we don't incorrectly attribute subsequent lines to the previous tag if it was a single-line tag.
    if (currentTag && line.length === 0) {
        currentTag = "";
    }
  }

  const params = [];
  // Parse each collected raw parameter description into structured object.
  for (const rawParam of rawParams) {
    try {
      const parsed = parseParam(rawParam, funcName);
      if (parsed) {
        params.push(parsed);
      }
    } catch (err) {
      // Re-throw errors with context about the function and parameter for better debugging.
      throw new Error(`Failed to parse @property for function '${funcName}': ${err.message}`);
    }
  }
  return {
    description: description.trim(), // Trim the final description.
    params, // Return the array of parsed parameter objects.
  };
}

/**
 * Parses a single @property JSDoc line into structured parameter data.
 * Extracts type, name (handling optionality), and description.
 * @param {string} rawParam - The raw string for a parameter, e.g., "{string} name - Description".
 * @param {string} funcName - The name of the function for error reporting context.
 * @throws {Error} If the raw parameter format is invalid according to the expected regex.
 * @returns {{name: string, property: object, required: boolean}} An object representing the parsed parameter.
 */
function parseParam(rawParam, funcName) {
  // Regex to capture: {type} name - description
  // - {type}: mandatory, captured in group 1.
  // - name: can be optional (e.g., '[name]'), captured in group 2.
  // - - description: optional description, captured in group 3.
  const regex = /^{([^}]+)}\s*(\S+)(?:\s*(?:-?\s*)(\S.*))?$/;
  const match = regex.exec(rawParam.trim());

  // If the regex does not match, the format is invalid.
  if (!match) {
    throw new Error(`Invalid format: "${rawParam}". Expected format like "{type} name - description"`);
  }

  const typeFull = match[1];
  let name = match[2];
  const description = match[3] || "";

  // Skip generic 'args' parameter if it's just a placeholder for the properties
  if (name === "args" && (typeFull === "Args" || typeFull === "Object")) {
    return null;
  }

  let required = true; // Assume parameter is required by default.
  // Check if the parameter name is enclosed in square brackets, indicating it's optional.
  if (/^\[.*\]$/.test(name)) {
    name = name.slice(1, -1); // Remove the brackets to get the actual parameter name.
    required = false; // Mark parameter as not required.
  }

  // Build the JSON schema property object based on the extracted type and description.
  const property = buildProperty(typeFull, description);
  return { name, property, required }; // Return structured parameter data.
}

/**
 * Builds a JSON schema property object from a JSDoc type string and a description.
 * Maps common JSDoc types to corresponding JSON Schema types.
 * @param {string} typeFull - The full type string from JSDoc (e.g., "string", "integer", "string[]", "boolean", "'choice1'|'choice2'", "string?").
 * @param {string} description - The description for the property, which becomes the 'description' field in the schema.
 * @throws {Error} If the JSDoc type string is unsupported.
 * @returns {object} A JSON schema property object (e.g., { type: "string", description: "..." }).
 */
function buildProperty(typeFull, description) {
  let type = typeFull.toLowerCase(); // Normalize type to lowercase for easier comparison.
  const property = {}; // Initialize the property object.

  // Handle optional types indicated by a trailing '?'. The 'required' flag on the parameter level
  // is the primary mechanism for optionality, but we also clean the type string itself.
  if (type.endsWith("?")) {
    type = type.slice(0, -1); // Remove the '?' from the type string.
  }

  if (type.includes("|")) {
    // Handle enum types (e.g., "'choice1'|'choice2'").
    property.type = "string"; // Enums are typically represented as strings.
    // Extract enum values, removing quotes and splitting by '|'.
    property.enum = type.replace(/'/g, "").split("|");
  } else if (type === "boolean") {
    property.type = "boolean";
  } else if (type === "string") {
    property.type = "string";
  } else if (type === "integer") {
    property.type = "integer";
  } else if (type === "number") {
    property.type = "number";
  } else if (type === "string[]") {
    // Handle array of strings.
    property.type = "array";
    property.items = { type: "string" }; // Assume array items are strings by default if not specified otherwise.
  } else {
    // If the type is not recognized, default to string instead of throwing an error.
    property.type = "string";
  }

  property.description = description; // Assign the description to the schema property.
  return property;
}

/**
 * Builds the final JSON declaration object for a function.
 * This object includes the function's name, description, and a JSON schema for its parameters.
 * @param {string} funcName - The name of the function.
 * @param {string} description - The function's description text.
 * @param {Param[]} params - An array of parsed parameter objects, each containing {name, property, required}.
 * @returns {object} The complete JSON declaration object ready for output.
 */
function buildDeclaration(funcName, description, params) {
  const declaration = {
    name: funcName, // The name of the function.
    description, // The function's description.
    parameters: { // JSON schema for the function's parameters.
      type: "object", // Parameters are expected as a JSON object.
      properties: {}, // Object to hold individual parameter schemas.
    },
  };
  const schema = declaration.parameters; // Reference to the parameters schema for easier access.
  const requiredParams = []; // Array to list names of required parameters.

  // Iterate through the parsed parameters to populate the schema.
  for (const { name, property, required } of params) {
    schema.properties[name] = property; // Add the parameter schema to properties.
    if (required) {
      requiredParams.push(name); // Add parameter name to the list of required parameters.
    }
  }

  // If there are any required parameters, add the 'required' array to the schema.
  if (requiredParams.length > 0) {
    schema.required = requiredParams;
  }
  return declaration; // Return the fully constructed declaration object.
}

/**
 * Extracts the base name of a file path without its extension.
 * Handles cases with no extension or multiple dots in the filename.
 * E.g., "tools/fs_ls.js" -> "fs_ls".
 * @param {string} filePath - The path to the file.
 * @returns {string} The base name of the file (e.g., "fs_ls").
 */
function getBasename(filePath) {
  // Extract the filename part from the path, handling both '/' and '\' separators.
  const filenameWithExt = filePath.split(/[/\\]/).pop();
  if (!filenameWithExt) return ""; // Return empty string if path is empty or malformed.

  const lastDotIndex = filenameWithExt.lastIndexOf(".");

  // If no dot is found, or if the dot is the first character (e.g., ".bashrc"), return the whole filename.
  if (lastDotIndex === -1 || lastDotIndex === 0) {
    return filenameWithExt;
  }

  // Return the substring before the last dot, effectively removing the extension.
  return filenameWithExt.substring(0, lastDotIndex);
}

// Execute the main function when the script is run.
main();
