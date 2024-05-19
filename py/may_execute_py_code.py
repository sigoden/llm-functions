def declarate():
  return {
    "name": "may_execute_py_code",
    "description": "Runs the python code.",
    "parameters": {
      "type": "object",
      "properties": {
        "code": {
          "type": "string",
          "description": "Python code to execute, such as `print(\"hello world\")`"
        }
      },
      "required": [
        "code"
      ]
    }
  }


def run(data):
  exec(data["code"])

# For compatibility with module.exports 
class Module:
  pass

module = Module()
module.declarate = declarate
module.run = run