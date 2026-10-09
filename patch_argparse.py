import re

with open("tools/bybit_wbta.py") as f:
    code = f.read()

# Replace everything from def main() down with argparse logic
new_main = """
import argparse
import sys

def main() -> None:
    parser = argparse.ArgumentParser(description="Bybit WhaleBot Technical Observatory")
    parser.add_argument("--symbol", type=str, default=None, help="Trading pair")
    parser.add_argument("--interval", type=str, default=None, help="Timeframe")
    parser.add_argument("--delay", type=int, default=None, help="Refresh seconds")
    parser.add_argument("--use-tor", type=str, default=None, dest="use_tor")
    parser.add_argument("--once", type=str, default=None)
    parser.add_argument("--json-out", type=str, default=None, dest="json_out")

    # Only parse known args so aichat can append other stuff if needed, though parse_args() is safer
    args, _ = parser.parse_known_args()

    # If run interactively with no args, prompt user
    if len(sys.argv) == 1 and sys.stdin.isatty():
        print(f"\\n{NEON_CYAN}{'═'*54}{RESET}")
        print(f"{NEON_PURPLE}{BRIGHT}  NEON MARKET TREND OBSERVATORY  v3.0{RESET}")
        print(f"{NEON_CYAN}  L2 Orderbook | Microstructure | Funding | OI | Flow{RESET}")
        print(f"{NEON_CYAN}{'═'*54}{RESET}\\n")

        args.symbol   = (input(f"{NEON_CYAN}Target symbol   (default: BTCUSDT) : {RESET}").strip() or "BTCUSDT")
        args.interval = (input(f"{NEON_CYAN}Timeframe       (1/5/15/60/D)      : {RESET}").strip() or "15")
        delay_s       = input(f"{NEON_CYAN}Refresh seconds (default: 20)      : {RESET}").strip()
        args.delay    = int(delay_s) if delay_s.isdigit() else 20
        # When interactive, don't use json_out by default unless specified
        if args.once is None: args.once = "false"
        if args.json_out is None: args.json_out = "false"
    
    # We call run() with the parsed arguments
    try:
        run(
            symbol=args.symbol,
            interval=args.interval,
            delay=args.delay,
            use_tor=args.use_tor,
            once=args.once,
            json_out=args.json_out
        )
    except KeyboardInterrupt:
        print(f"\\n\\n{NEON_PURPLE}The observatory screen goes dark.  Safe travels, seeker.{RESET}\\n")
        sys.exit(0)
    except Exception as e:
        if _coerce_bool(args.json_out, True):
            import json
            print(json.dumps({"success": False, "error": str(e)}))
        else:
            print(f"\\n{NEON_RED}[FATAL ERROR] {e}{RESET}")
        sys.exit(1)

__all__ = ["run"]

if __name__ == "__main__":
    main()
"""

# Find the start of def main() and cut it off, then append the new main block
start_idx = code.find("def main() -> None:")
if start_idx != -1:
    # First extract the _coerce_bool and run functions from the old code, since they were at the bottom
    # We'll just define them ABOVE new_main
    run_funcs = code[code.find("def _coerce_bool") :]
    # Remove the `if __name__ == "__main__": main()` if it exists inside run_funcs
    run_funcs = re.sub(r'if __name__ == "__main__":\s+main\(\)', "", run_funcs)

    code = code[:start_idx] + run_funcs + "\\n" + new_main
else:
    print("Could not find def main()")

with open("tools/bybit_wbta.py", "w") as f:
    f.write(code)
