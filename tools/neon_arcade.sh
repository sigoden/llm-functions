#!/data/data/com.termux/files/usr/bin/env bash

# ------------------------------------------------------------
# neon_arcade.sh – arcade‑style neon output
# @describe Arcade-style neon output command wrapper.
# ------------------------------------------------------------
# This wrapper inherits *all* functionality from execute_command.sh
# (colour handling, timeout logic, logging, etc.) and simply
# re‑defines a handful of variables to give the UI a high‑contrast,
# retro‑arcade feel.
# ------------------------------------------------------------

# 1️⃣  Source the shared engine
source ./execute_command.sh

# ------------------------------------------------------------
# 2️⃣  Pick the “toxic” palette but crank up the saturation
# ------------------------------------------------------------
CYBER_THEME="toxic"                     # activates the toxic colour set
# The numeric values are 256‑color ANSI codes.  Feel free to replace
# them with any colour you like (look them up on https://jonasjacek.github.io/colors/).
CYBER_PINK=' [38;5;118m'                # neon toxic green (bright)
CYBER_CYAN=' [38;5;82m'                 # vivid lime‑cyan
CYBER_GREEN=' [38;5;46m'                # pure matrix green
CYBER_ORANGE=' [38;5;172m'              # dark ochre (high‑contrast)
CYBER_YELLOW=' [38;5;190m'              # radioactive yellow

# ------------------------------------------------------------
# 3️⃣  Use block‑elements that look like an arcade cabinet
# ------------------------------------------------------------
BLOCK_TL='⎡'   # top‑left corner
BLOCK_TR='⎤'   # top‑right corner
BLOCK_BL='⎦'   # bottom‑left corner
BLOCK_BR='⎥'   # bottom‑right corner
BLOCK_V='▒'    # vertical filler (light shade)
BLOCK_H='█'    # horizontal filler (full block)
BLOCK_LT='╣'   # left‑top tee
BLOCK_RT='╗'   # right‑top tee
BLOCK_FULL='▓' # solid block for a heavy frame

# ------------------------------------------------------------
# 4️⃣  Header with an arcade‑style banner
# ------------------------------------------------------------
HEADER_TEXT="🕹️  ARCADE MODE  🕹️"

# ------------------------------------------------------------
# 5️⃣  Sample command – simulate a high‑score table
# ------------------------------------------------------------
# You can replace this with *any* command you want to wrap.
EXEC_CMD="printf 'Score: %06d\\nCoins: %02d\\n' 999999 99 && sleep 0.5"

# ------------------------------------------------------------
# 6️⃣  Hand over control to the shared engine
# ------------------------------------------------------------
main
