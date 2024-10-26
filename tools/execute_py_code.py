import io
import sys

def run(code: str):
    """Execute the python code.
    Args:
        code: Python code to execute, such as `print("hello world")`
    """
    old_stdout = sys.stdout
    output = io.StringIO()
    sys.stdout = output

    exec(code)

    sys.stdout = old_stdout
    return output.getvalue()