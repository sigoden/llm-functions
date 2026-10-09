from pathlib import Path

file_path = Path('/data/data/com.termux/files/home/.config/aichat/llm-functions/tools/bybit_terminal.py')
lines = file_path.read_text().splitlines()

# Lines to move: 301 to 380 (1-based index 301 is 300)
methods_to_move = lines[300:380]
remaining_lines = lines[:300] + lines[380:]

# Find where BybitRealm class starts
class_start_idx = 0
for i, line in enumerate(remaining_lines):
    if 'class BybitRealm:' in line:
        class_start_idx = i
        break

# Find where __init__ starts
init_idx = 0
for i in range(class_start_idx, len(remaining_lines)):
    if 'def __init__' in remaining_lines[i]:
        # Find the end of __init__ (starts next method)
        for j in range(i + 1, len(remaining_lines)):
            if 'def ' in remaining_lines[j]:
                init_idx = j
                break
        break

# Insert methods
new_lines = remaining_lines[:init_idx] + methods_to_move + remaining_lines[init_idx:]
file_path.write_text('\n'.join(new_lines))
