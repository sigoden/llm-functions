import os

def run(file_path: str, start_line: int, end_line: int):
    """Read a specific range of lines from a file. Useful for reading large logs or files without overloading context.
    Args:
        file_path: The path to the file to read.
        start_line: The line number to start reading from (1-based index).
        end_line: The line number to stop reading at.
    """
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        return

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f, start=1):
                if i > end_line:
                    break
                if i >= start_line:
                    print(f"{i}: {line}", end='')
    except Exception as e:
        print(f"Error reading file: {e}")
