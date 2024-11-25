import ast
import io
import sys
from contextlib import redirect_stdout

def run(code: str):
    """Execute the python code.
    Args:
        code: Python code to execute, such as `print("hello world")`
    """
    output = io.StringIO()
    with redirect_stdout(output):
        value = exec_with_return(code, {}, {})

        if value is not None:
            output.write(str(value)) 

    return output.getvalue()

def exec_with_return(code: str, globals: dict, locals: dict):
    a = ast.parse(code)
    last_expression = None
    if a.body:
        if isinstance(a_last := a.body[-1], ast.Expr):
            last_expression = ast.unparse(a.body.pop())
        elif isinstance(a_last, ast.Assign):
            last_expression = ast.unparse(a_last.targets[0])
        elif isinstance(a_last, (ast.AnnAssign, ast.AugAssign)):
            last_expression = ast.unparse(a_last.target)
    exec(ast.unparse(a), globals, locals)
    if last_expression:
        return eval(last_expression, globals, locals)