import sys
import math

try:
    import tiktoken
except ImportError:
    tiktoken = None

def run(text: str, model: str = "gpt-4"):
    """Count the number of tokens in a text string. Useful for checking if data fits in context window.
    Args:
        text: The text string to analyze.
        model: The model encoding to use (default: gpt-4).
    """
    if tiktoken:
        try:
            encoding = tiktoken.encoding_for_model(model)
            count = len(encoding.encode(text))
            print(f"Token count ({model}): {count}")
            return
        except Exception as e:
            print(f"Error using tiktoken: {e}")
            # Fallthrough to heuristic
    
    # Fallback heuristic: English text averages ~4 characters per token
    heuristic_count = math.ceil(len(text) / 4)
    print(f"Estimated Token count (Heuristic: chars/4): {heuristic_count}")
    print("Note: Install 'tiktoken' python package for exact counts.")
