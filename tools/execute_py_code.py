import io
import sys

def run(code: str):
    """Execute the python code.
    Args:
        code: Python code to execute, such as `print("hello world")`
    """
    old_stdout = sys.stdout
    new_stdout = io.StringIO()
    sys.stdout = new_stdout

    exec(code)

    sys.stdout = old_stdout
    return new_stdout.getvalue()