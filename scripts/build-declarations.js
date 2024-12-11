#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const TOOL_ENTRY_FUNC = "run";

function main() {
  const scriptfile = process.argv[2];
  const isTool = path.dirname(scriptfile) == "tools";
  const contents = fs.readFileSync(process.argv[2], "utf8");
  const functions = extractFunctions(contents, isTool);
  let declarations = [];
  for (const { funcName, jsdoc } of functions) {
    const { description, params } = parseJsDoc(jsdoc, funcName);
    if (!description) continue;
    const declaration = buildDeclaration(funcName, description, params);
    declarations.push(declaration);
  }
  if (isTool) {
    const name = getBasename(scriptfile);
    if (declarations.length > 0) {
      declarations = declarations.slice(0, 1);
      declarations[0].name = name;
    }
  }
  console.log(JSON.stringify(declarations, null, 2));
}

/**
 * @param {string} contents
 * @param {bool} isTool
 */
function extractFunctions(contents, isTool) {
  const output = [];
  const lines = contents.split("\n");
  let isInComment = false;
  let jsdoc = "";
  let incompleteComment = "";
  for (let line of lines) {
    if (/^\s*\/\*/.test(line)) {
      isInComment = true;
      incompleteComment += `\n${line}`;
    } else if (/^\s*\*\//.test(line)) {
      isInComment = false;
      incompleteComment += `\n${line}`;
      jsdoc = incompleteComment;
      incompleteComment = "";
    } else if (isInComment) {
      incompleteComment += `\n${line}`;
    } else {
      if (!jsdoc || line.trim() === "") {
        continue;
      }
      if (isTool) {
        if (new RegExp(`^export (async )?function ${TOOL_ENTRY_FUNC}|^exports\.${TOOL_ENTRY_FUNC}`).test(line)) {
          output.push({
            funcName: TOOL_ENTRY_FUNC,
            jsdoc,
          });
        }
      } else {
        let match = /^export (async )?function ([A-Za-z0-9_]+)/.exec(line);
        let funcName = null;
        if (match) {
          funcName = match[2];
        }
        if (!funcName) {
          match = /^exports\.([A-Za-z0-9_]+) = (async )?function /.exec(line);
          if (match) {
            funcName = match[1];
          }
        }
        if (funcName && !funcName.startsWith("_")) {
          output.push({ funcName, jsdoc });
        }
      }
      jsdoc = "";
    }
  }
  return output;
}

/**
 * @param {string} jsdoc
 * @param {string} funcName,
 */
function parseJsDoc(jsdoc, funcName) {
  const lines = jsdoc.split("\n");
  let description = "";
  const rawParams = [];
  let tag = "";
  for (let line of lines) {
    line = line.replace(/^\s*(\/\*\*|\*\/|\*)/, "").trim();
    let match = /^@(\w+)/.exec(line);
    if (match) {
      tag = match[1];
    }
    if (!tag) {
      description += `\n${line}`;
    } else if (tag == "property") {
      if (match) {
        rawParams.push(line.slice(tag.length + 1).trim());
      } else {
        rawParams[rawParams.length - 1] += `\n${line}`;
      }
    }
  }
  const params = [];
  for (const rawParam of rawParams) {
    try {
      params.push(parseParam(rawParam));
    } catch (err) {
      throw new Error(
        `Unable to parse function '${funcName}' of jsdoc '@property ${rawParam}'`,
      );
    }
  }
  return {
    description: description.trim(),
    params,
  };
}

/**
 * @typedef {ReturnType<parseParam>} Param
 */

/**
 * @param {string} rawParam
 */
function parseParam(rawParam) {
  const regex = /^{([^}]+)} +(\S+)( *- +| +)?/;
  const match = regex.exec(rawParam);
  if (!match) {
    throw new Error(`Invalid jsdoc comment`);
  }
  const type = match[1];
  let name = match[2];
  const description = rawParam.replace(regex, "");

  let required = true;
  if (/^\[.*\]$/.test(name)) {
    name = name.slice(1, -1);
    required = false;
  }
  let property = buildProperty(type, description);
  return { name, property, required };
}

/**
 * @param {string} type
 * @param {string} description
 */
function buildProperty(type, description) {
  type = type.toLowerCase();
  const property = {};
  if (type.includes("|")) {
    property.type = "string";
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
    property.type = "array";
    property.items = { type: "string" };
  } else {
    throw new Error(`Unsupported type '${type}'`);
  }
  property.description = description;
  return property;
}

/**
 * @param {string} name
 * @param {string} description
 * @param {Param[]} params
 */
function buildDeclaration(name, description, params) {
  const declaration = {
    name,
    description,
    parameters: {
      type: "object",
      properties: {},
    },
  };
  const schema = declaration.parameters;
  const requiredParams = [];
  for (const { name, property, required } of params) {
    schema.properties[name] = property;
    if (required) {
      requiredParams.push(name);
    }
  }
  if (requiredParams.length > 0) {
    schema.required = requiredParams;
  }
  return declaration;
}

/**
 * @param {string} filePath
 */
function getBasename(filePath) {
  const filenameWithExt = filePath.split(/[/\\]/).pop();

  const lastDotIndex = filenameWithExt.lastIndexOf(".");

  if (lastDotIndex === -1) {
    return filenameWithExt;
  }

  return filenameWithExt.substring(0, lastDotIndex);
}

main();
