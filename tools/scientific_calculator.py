#!/usr/bin/env python3
# ==============================================================================
# scientific_calculator.py — Pyrmethus AIChat Advanced Scientific Calculator Tool v1.1.0-UPGRADED
# argc/aichat compatible · Human-Readable Colorized UI · Safe Expression Evaluation · Stats & Matrices
#
# @describe Perform advanced scientific calculations, statistical analysis, expression evaluation, and unit conversions.
#
# @meta require-tools aichat
#
# @option --expr <EXPR>                  Mathematical expression to evaluate (e.g., "sin(pi/4) + log(10)")
# @option --mode <MODE>                  Operation mode: eval/stats/matrix/convert (default: eval)
# @option --data <LIST_OR_FILE>          Comma-separated values or JSON array for statistics mode
# @option --matrix-a <JSON>              Matrix A for matrix operations (JSON 2D array or file path)
# @option --matrix-b <JSON>              Matrix B for matrix operations (JSON 2D array or file path)
# @option --matrix-op <OP>               Matrix operation: add, sub, mul, det, inv, transpose (default: mul)
# @option --from-unit <UNIT>             Source unit for conversion (e.g., deg, rad, m, ft, c, f, kg, lb)
# @option --to-unit <UNIT>               Target unit for conversion
# @option --value <NUM>                  Numeric value for unit conversion
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import cmath
import enum
import json
import logging
import math
import os
import re
import signal
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

__version__ = "1.1.0-UPGRADED"
__all__ = ["__version__", "execute_tool", "run"]

# ==============================================================================
# SECTION 1: Exit Codes & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INVALID_INPUT = 2
EXIT_CALCULATION_ERROR = 3


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Path, Enum, complex numbers, datetime, bytes, and sets safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, enum.Enum):
            return obj.value
        if isinstance(obj, complex):
            return {"real": obj.real, "imag": obj.imag, "str": str(obj)}
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Helpers
# ==============================================================================

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# Comprehensive ANSI escape sequence stripping regex
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive terminal."""
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    """Print pre-formatted ANSI text to stderr by default."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a colorized box UI for terminal users to stderr."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 68
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [SCIENTIFIC CALCULATOR v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Mode:{RESET}     {NEON_YELLOW}{data.get('mode', 'eval')}{RESET}"
    )

    mode = data.get("mode")
    if mode == "eval":
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Expr:{RESET}     {data.get('expr', 'N/A')}"
        )
    elif mode == "stats":
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Count:{RESET}    {data.get('count', 0)}"
        )
    elif mode == "matrix":
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Op:{RESET}       {data.get('matrix_op', 'N/A')}"
        )
    elif mode == "convert":
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Convert:{RESET}  {data.get('value')} {data.get('from_unit')} → {data.get('to_unit')}"
        )

    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if success and "result" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        res_str = str(data["result"])
        if len(res_str) > 55:
            res_str = res_str[:52] + "..."
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_GREEN}Result:{RESET}   {BOLD}{res_str}{RESET}"
        )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}    {data['error']}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Calculator Engines (Safe Eval, Stats, Gauss Matrices, Conversions)
# ==============================================================================


class GracefulShutdown:
    """Signal handler for graceful cancellation."""

    def __init__(self) -> None:
        self.interrupted = False
        try:
            self._old_sigint = signal.signal(signal.SIGINT, self._handler)
            self._old_sigterm = signal.signal(signal.SIGTERM, self._handler)
        except ValueError:
            self._old_sigint = signal.SIG_DFL
            self._old_sigterm = signal.SIG_DFL

    def _handler(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        try:
            signal.signal(signal.SIGINT, self._old_sigint)
            signal.signal(signal.SIGTERM, self._old_sigterm)
        except ValueError:
            pass


def _safe_eval(expr: str) -> Any:
    """Safely evaluate mathematical expressions using math, cmath, and sandbox builtins."""
    safe_dict = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    safe_dict.update(
        {k: getattr(cmath, k) for k in dir(cmath) if not k.startswith("_")}
    )
    safe_dict.update(
        {
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "sum": sum,
            "pow": pow,
            "divmod": divmod,
            "complex": complex,
        }
    )

    cleaned = expr.strip()
    return eval(cleaned, {"__builtins__": {}}, safe_dict)


def _parse_data_input(data_input: Any) -> list[float]:
    """Parse statistics data input from comma-separated string, JSON list, or file path."""
    if isinstance(data_input, list):
        return [float(x) for x in data_input]
    if isinstance(data_input, str):
        cleaned = data_input.strip()
        if cleaned.startswith("[") or cleaned.startswith("{"):
            parsed = json.loads(cleaned)
            return [float(x) for x in parsed]
        try:
            path_obj = Path(cleaned).expanduser().resolve()
            if path_obj.is_file():
                content = path_obj.read_text(encoding="utf-8")
                if content.strip().startswith("["):
                    parsed = json.loads(content)
                    return [float(x) for x in parsed]
                else:
                    return [
                        float(line.strip())
                        for line in content.splitlines()
                        if line.strip() and not line.startswith("#")
                    ]
        except (OSError, ValueError):
            pass
        parts = [p.strip() for p in cleaned.replace(",", " ").split() if p.strip()]
        return [float(p) for p in parts]
    return []


def _parse_matrix(matrix_input: Any) -> list[list[float]]:
    """Parse 2D matrix input from JSON string or file path."""
    if isinstance(matrix_input, list):
        return [[float(val) for val in row] for row in matrix_input]
    if isinstance(matrix_input, str):
        cleaned = matrix_input.strip()
        if cleaned.startswith("["):
            return json.loads(cleaned)
        try:
            path_obj = Path(cleaned).expanduser().resolve()
            if path_obj.is_file():
                return json.loads(path_obj.read_text(encoding="utf-8"))
        except Exception:
            pass
        raise ValueError(f"Invalid matrix input: {matrix_input}")
    raise ValueError("Matrix input must be a list or JSON string/file.")


def _matrix_add(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    if len(a) != len(b) or len(a[0]) != len(b[0]):
        raise ValueError("Matrix dimensions must match for addition.")
    return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def _matrix_sub(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    if len(a) != len(b) or len(a[0]) != len(b[0]):
        raise ValueError("Matrix dimensions must match for subtraction.")
    return [[a[i][j] - b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def _matrix_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    r_a, c_a = len(a), len(a[0])
    r_b, c_b = len(b), len(b[0])
    if c_a != r_b:
        raise ValueError(
            f"Cannot multiply matrices of shape ({r_a}x{c_a}) and ({r_b}x{c_b})."
        )
    return [
        [sum(a[i][k] * b[k][j] for k in range(c_a)) for j in range(c_b)]
        for i in range(r_a)
    ]


def _matrix_transpose(a: list[list[float]]) -> list[list[float]]:
    return [[a[j][i] for j in range(len(a))] for i in range(len(a[0]))]


def _matrix_det(matrix: list[list[float]]) -> float:
    """Compute determinant in O(n^3) time using Gaussian elimination."""
    n = len(matrix)
    if n != len(matrix[0]):
        raise ValueError("Determinant requires a square matrix.")
    mat = [row[:] for row in matrix]
    det = 1.0
    for i in range(n):
        max_row = i
        for k in range(i + 1, n):
            if abs(mat[k][i]) > abs(mat[max_row][i]):
                max_row = k
        if max_row != i:
            mat[i], mat[max_row] = mat[max_row], mat[i]
            det *= -1
        if abs(mat[i][i]) < 1e-12:
            return 0.0
        det *= mat[i][i]
        for k in range(i + 1, n):
            c = mat[k][i] / mat[i][i]
            for j in range(i, n):
                mat[k][j] -= c * mat[i][j]
    return det


def _matrix_inv(matrix: list[list[float]]) -> list[list[float]]:
    """Compute matrix inverse in O(n^3) time using Gauss-Jordan elimination."""
    n = len(matrix)
    if n != len(matrix[0]):
        raise ValueError("Inverse requires a square matrix.")
    mat = [
        row[:] + [1.0 if i == j else 0.0 for j in range(n)]
        for i, row in enumerate(matrix)
    ]

    for i in range(n):
        max_row = i
        for k in range(i + 1, n):
            if abs(mat[k][i]) > abs(mat[max_row][i]):
                max_row = k
        if max_row != i:
            mat[i], mat[max_row] = mat[max_row], mat[i]
        if abs(mat[i][i]) < 1e-12:
            raise ValueError("Matrix is singular and cannot be inverted.")

        pivot = mat[i][i]
        for j in range(2 * n):
            mat[i][j] /= pivot

        for k in range(n):
            if k != i:
                factor = mat[k][i]
                for j in range(2 * n):
                    mat[k][j] -= factor * mat[i][j]

    return [row[n:] for row in mat]


def _convert_units(value: float, from_u: str, to_u: str) -> float:
    """Convert between extended scientific units (temperature, angles, length, mass, data)."""
    f = from_u.lower().strip()
    t = to_u.lower().strip()

    # Temperature
    if f in {"c", "celsius"} and t in {"f", "fahrenheit"}:
        return (value * 9 / 5) + 32
    if f in {"f", "fahrenheit"} and t in {"c", "celsius"}:
        return (value - 32) * 5 / 9
    if f in {"c", "celsius"} and t in {"k", "kelvin"}:
        return value + 273.15
    if f in {"k", "kelvin"} and t in {"c", "celsius"}:
        return value - 273.15
    if f in {"f", "fahrenheit"} and t in {"k", "kelvin"}:
        return (value - 32) * 5 / 9 + 273.15
    if f in {"k", "kelvin"} and t in {"f", "fahrenheit"}:
        return (value - 273.15) * 9 / 5 + 32

    # Angles
    if f in {"deg", "degrees"} and t in {"rad", "radians"}:
        return value * math.pi / 180.0
    if f in {"rad", "radians"} and t in {"deg", "degrees"}:
        return value * 180.0 / math.pi

    # Length (base meters)
    lengths = {
        "m": 1.0,
        "meter": 1.0,
        "meters": 1.0,
        "cm": 0.01,
        "centimeter": 0.01,
        "mm": 0.001,
        "millimeter": 0.001,
        "km": 1000.0,
        "kilometer": 1000.0,
        "mi": 1609.344,
        "mile": 1609.344,
        "ft": 0.3048,
        "foot": 0.3048,
        "feet": 0.3048,
        "in": 0.0254,
        "inch": 0.0254,
        "inches": 0.0254,
    }
    if f in lengths and t in lengths:
        val_in_meters = value * lengths[f]
        return val_in_meters / lengths[t]

    # Mass (base grams)
    masses = {
        "g": 1.0,
        "gram": 1.0,
        "grams": 1.0,
        "kg": 1000.0,
        "kilogram": 1000.0,
        "mg": 0.001,
        "milligram": 0.001,
        "lb": 453.59237,
        "pound": 453.59237,
        "pounds": 453.59237,
        "oz": 28.349523125,
        "ounce": 28.349523125,
        "ounces": 28.349523125,
    }
    if f in masses and t in masses:
        val_in_grams = value * masses[f]
        return val_in_grams / masses[t]

    # Data Storage (base bytes)
    data_units = {
        "b": 1.0,
        "byte": 1.0,
        "bytes": 1.0,
        "kb": 1024.0,
        "kilobyte": 1024.0,
        "mb": 1024.0**2,
        "megabyte": 1024.0**2,
        "gb": 1024.0**3,
        "gigabyte": 1024.0**3,
        "tb": 1024.0**4,
        "terabyte": 1024.0**4,
    }
    if f in data_units and t in data_units:
        val_in_bytes = value * data_units[f]
        return val_in_bytes / data_units[t]

    raise ValueError(f"Unsupported unit conversion from '{from_u}' to '{to_u}'.")


# ==============================================================================
# SECTION 4: Core Tool Execution
# ==============================================================================


def execute_tool(
    expr: str | None = None,
    mode: str = "eval",
    data: Any | None = None,
    matrix_a: Any | None = None,
    matrix_b: Any | None = None,
    matrix_op: str = "mul",
    from_unit: str | None = None,
    to_unit: str | None = None,
    value: float | None = None,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()
    mode_lower = mode.lower().strip()
    allowed_modes = {"eval", "stats", "matrix", "convert"}

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Executing scientific calculator in mode: {mode_lower}")

    if mode_lower not in allowed_modes:
        return {
            "success": False,
            "error": f"Unknown mode '{mode}'. Allowed: {', '.join(allowed_modes)}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    shutdown = GracefulShutdown()

    try:
        result_data: Any = None

        if mode_lower == "eval":
            if not expr:
                return {
                    "success": False,
                    "error": "--expr is required for eval mode.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }
            if shutdown.interrupted:
                return {
                    "success": False,
                    "error": "Interrupted by user signal.",
                    "exit_code": EXIT_ERROR,
                    "duration_ms": 0.0,
                }
            result_data = _safe_eval(expr)

        elif mode_lower == "stats":
            if data is None:
                return {
                    "success": False,
                    "error": "--data is required for stats mode.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }
            numbers = _parse_data_input(data)
            if not numbers:
                return {
                    "success": False,
                    "error": "No valid numeric data provided for statistics.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }

            count = len(numbers)
            mean_val = statistics.mean(numbers)
            median_val = statistics.median(numbers)
            try:
                mode_val = statistics.mode(numbers)
            except statistics.StatisticsError:
                mode_val = "No unique mode"

            stdev_val = statistics.stdev(numbers) if count > 1 else 0.0
            variance_val = statistics.variance(numbers) if count > 1 else 0.0
            pstdev_val = statistics.pstdev(numbers) if count > 0 else 0.0
            pvariance_val = statistics.pvariance(numbers) if count > 0 else 0.0
            min_val = min(numbers)
            max_val = max(numbers)
            sum_val = sum(numbers)
            range_val = max_val - min_val

            result_data = {
                "count": count,
                "sum": sum_val,
                "mean": mean_val,
                "median": median_val,
                "mode": mode_val,
                "stdev": stdev_val,
                "variance": variance_val,
                "pstdev": pstdev_val,
                "pvariance": pvariance_val,
                "min": min_val,
                "max": max_val,
                "range": range_val,
            }

        elif mode_lower == "matrix":
            if matrix_a is None:
                return {
                    "success": False,
                    "error": "--matrix-a is required for matrix mode.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }
            mat_a = _parse_matrix(matrix_a)
            op = matrix_op.lower().strip()

            if op in {"add", "sub", "mul"}:
                if matrix_b is None:
                    return {
                        "success": False,
                        "error": f"--matrix-b is required for matrix operation '{op}'.",
                        "exit_code": EXIT_INVALID_INPUT,
                        "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                    }
                mat_b = _parse_matrix(matrix_b)
                if op == "add":
                    result_data = _matrix_add(mat_a, mat_b)
                elif op == "sub":
                    result_data = _matrix_sub(mat_a, mat_b)
                elif op == "mul":
                    result_data = _matrix_mul(mat_a, mat_b)
            elif op == "transpose":
                result_data = _matrix_transpose(mat_a)
            elif op == "det":
                result_data = _matrix_det(mat_a)
            elif op == "inv":
                result_data = _matrix_inv(mat_a)
            else:
                return {
                    "success": False,
                    "error": f"Unknown matrix operation '{op}'. Allowed: add, sub, mul, det, inv, transpose.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }

        elif mode_lower == "convert":
            if value is None or not from_unit or not to_unit:
                return {
                    "success": False,
                    "error": "--value, --from-unit, and --to-unit are all required for convert mode.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }
            result_data = _convert_units(float(value), from_unit, to_unit)

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": True,
            "mode": mode_lower,
            "expr": expr,
            "count": result_data.get("count")
            if isinstance(result_data, dict) and "count" in result_data
            else None,
            "matrix_op": matrix_op if mode_lower == "matrix" else None,
            "from_unit": from_unit,
            "to_unit": to_unit,
            "value": value,
            "result": result_data,
            "exit_code": EXIT_SUCCESS,
            "duration_ms": duration_ms,
        }

    except (
        ZeroDivisionError,
        OverflowError,
        ValueError,
        SyntaxError,
        NameError,
    ) as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "mode": mode_lower,
            "error": f"Calculation error ({type(exc).__name__}): {exc}",
            "exit_code": EXIT_CALCULATION_ERROR,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "mode": mode_lower,
            "error": f"Execution failure: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 5: Output Routing & Entrypoints
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write JSON payload to LLM_OUTPUT."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    direct_targets = {"/dev/stdout", "/dev/fd/1", "-"}
    if out_path in direct_targets:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


def run(
    expr: str | None = None,
    mode: Literal["eval", "stats", "matrix", "convert"] = "eval",
    data: Any | None = None,
    matrix_a: Any | None = None,
    matrix_b: Any | None = None,
    matrix_op: str = "mul",
    from_unit: str | None = None,
    to_unit: str | None = None,
    value: float | None = None,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """AIChat Programmatic Entrypoint."""
    result = execute_tool(
        expr=expr,
        mode=mode,
        data=data,
        matrix_a=matrix_a,
        matrix_b=matrix_b,
        matrix_op=matrix_op,
        from_unit=from_unit,
        to_unit=to_unit,
        value=value,
        no_color=no_color,
        verbose=verbose,
    )
    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scientific_calculator.py",
        description=f"Pyrmethus Advanced Scientific Calculator v{__version__}",
    )
    parser.add_argument("--expr", "-e", help="Mathematical expression to evaluate")
    parser.add_argument(
        "--mode",
        "-m",
        choices=["eval", "stats", "matrix", "convert"],
        default="eval",
        help="Calculation mode",
    )
    parser.add_argument("--data", "-d", help="Data list or file path for statistics")
    parser.add_argument("--matrix-a", dest="matrix_a", help="Matrix A (JSON or path)")
    parser.add_argument("--matrix-b", dest="matrix_b", help="Matrix B (JSON or path)")
    parser.add_argument(
        "--matrix-op", dest="matrix_op", default="mul", help="Matrix operation"
    )
    parser.add_argument("--from-unit", dest="from_unit", help="Source unit")
    parser.add_argument("--to-unit", dest="to_unit", help="Target unit")
    parser.add_argument("--value", type=float, help="Numeric value for conversion")
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable colors",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable verbose logging",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = run(
        expr=args.expr,
        mode=args.mode,
        data=args.data,
        matrix_a=args.matrix_a,
        matrix_b=args.matrix_b,
        matrix_op=args.matrix_op,
        from_unit=args.from_unit,
        to_unit=args.to_unit,
        value=args.value,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
