from pathlib import Path

file_path = Path('/data/data/com.termux/files/home/.config/aichat/llm-functions/tools/bybit_terminal.py')
lines = file_path.read_text().splitlines()

# Lines to move: 302 to 380 (inclusive of micro_scalp through summary)
# Based on grep, micro_scalp starts after the comment on ~line 297/300
methods_to_move = lines[297:380]

# Remaining lines
remaining_lines = lines[:297] + lines[380:]

# Find BybitRealm class definition
class_def_idx = 0
for i, line in enumerate(remaining_lines):
    if 'class BybitRealm:' in line:
        class_def_idx = i
        break

# Insert methods immediately after class definition (or after __init__ to be safer)
# Let's insert after __init__ (search for it)
init_idx = 0
for i in range(class_def_idx, len(remaining_lines)):
    if 'def __init__' in remaining_lines[i]:
        # Find end of __init__
        for j in range(i + 1, len(remaining_lines)):
            if 'def ' in remaining_lines[j]:
                init_idx = j
                break
        break

# Join and write
new_lines = remaining_lines[:init_idx] + methods_to_move + remaining_lines[init_idx:]
file_path.write_text('\n'.join(new_lines))
