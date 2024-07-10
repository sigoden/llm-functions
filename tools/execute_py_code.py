def run(code: str):
    """Runs the python code.
    Args:
        code: Python code to execute, such as `print("hello world")`
    """
    return exec(code)
