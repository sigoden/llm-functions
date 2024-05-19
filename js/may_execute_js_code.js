function declarate() {
  return {
    "name": "may_execute_js_code",
    "description": "Runs the javascript code in node.js.",
    "parameters": {
      "type": "object",
      "properties": {
        "code": {
          "type": "string",
          "description": "Javascript code to execute, such as `console.log(\"hello world\")`"
        }
      },
      "required": [
        "code"
      ]
    }
  }
}

function run(data) {
  eval(data.code)
}

exports.declarate = declarate;
exports.run = run;