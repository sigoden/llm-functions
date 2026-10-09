/**
 * demo_js.js — JavaScript Tool Demo
 * 
 * Demonstrates how to create a tool using JavaScript with various parameter types.
 * Supports: required/optional strings, enums, integers, numbers, booleans, arrays.
 * 
 * @describe Demonstrate how to create a tool using JavaScript
 * @option --string! <TEXT> Define a required string property
 * @option --string-enum! <ENUM> Define a required string property with enum (foo|bar)
 * @option --string-optional <TEXT> Define an optional string property
 * @option --boolean Define a boolean property
 * @option --integer! <NUM> Define a required integer property
 * @option --number! <NUM> Define a required number property
 * @option --array!* <TEXT> Define a required string array property
 * @option --array-optional* <TEXT> Define an optional string array property
 * 
 * @typedef {Object} Args
 * @property {string} string - Required string
 * @property {'foo'|'bar'} string_enum - Required with enum
 * @property {string} [string_optional] - Optional string
 * @property {boolean} boolean - Boolean flag
 * @property {number} integer - Integer
 * @property {number} number - Number
 * @property {string[]} array - Required array
 * @property {string[]} [array_optional] - Optional array
 */
exports.run = function(args) {
    const { 
        string, 
        string_enum, 
        string_optional, 
        boolean, 
        integer, 
        number, 
        array, 
        array_optional 
    } = args;

    const output = {
        string,
        string_enum,
        string_optional,
        boolean,
        integer,
        number,
        array,
        array_optional
    };

    // Include LLM_ environment variables
    for (const key in process.env) {
        if (key.startsWith("LLM_")) {
            output[key] = process.env[key];
        }
    }

    return output;
};
