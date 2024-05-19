def declarate
  {
    "name": "may_execute_rb_code",
    "description": "Runs the ruby code.",
    "parameters": {
      "type": "object",
      "properties": {
        "code": {
          "type": "string",
          "description": "Ruby code to execute, such as `puts \"hello world\"`"
        }
      },
      "required": [
        "code"
      ]
    }
  }
end

def run(data)
  eval(data["code"])
end
