#!/usr/bin/env python3
"""
run-tool.py — Dynamic tool runner for LLM tool integrations.
"""

import importlib.util
import inspect
import json
import os
import re
import sys
import types
from typing import (
    Any,
    Dict,
    Literal,
    Tuple,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)


EXIT_SUCCESS = 0
EXIT_GENERAL_ERROR = 1
EXIT_INVALID_INPUT = 2
EXIT_FILE_NOT_FOUND = 3
EXIT_PERMISSION_DENIED = 4
EXIT_NETWORK_ERROR = 5
EXIT_TIMEOUT = 124
EXIT_COMMAND_NOT_FOUND = 127


class ToolError(Exception):
    """Error raised by the tool runner."""

    def __init__(
        self,
        message: str,
        exit_code: int = EXIT_GENERAL_ERROR,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def main() -> None:
    """Parse arguments, configure the environment, and execute the tool."""
    try:
        tool_name, raw_data = parse_argv("run-tool.py")
        tool_data = parse_raw_data(raw_data)

        root_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )

        setup_env(root_dir, tool_name)

        tool_path = os.path.join(
            root_dir,
            "tools",
            f"{tool_name}.py",
        )

        run(tool_name, tool_path, "run", tool_data)

    except ToolError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)

    except KeyboardInterrupt:
        print("\n[Aborted by user]", file=sys.stderr)
        sys.exit(130)

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(EXIT_GENERAL_ERROR)


def parse_argv(this_file_name: str) -> Tuple[str, str]:
    """Parse tool name and JSON data from command-line arguments."""
    argv = sys.argv

    if os.path.basename(argv[0]).endswith(this_file_name):
        if len(argv) < 3:
            raise ToolError(
                "Usage: ./run-tool.py <tool-name> <tool-data>",
                EXIT_INVALID_INPUT,
            )

        tool_name, tool_data = argv[1], argv[2]

    else:
        if len(argv) < 2:
            raise ToolError(
                "Usage: ./run-tool.py <tool-name> <tool-data>",
                EXIT_INVALID_INPUT,
            )

        tool_name, tool_data = os.path.basename(argv[0]), argv[1]

    if tool_name.endswith(".py"):
        tool_name = tool_name[:-3]

    if not tool_name or not tool_data:
        raise ToolError(
            "Usage: ./run-tool.py <tool-name> <tool-data>",
            EXIT_INVALID_INPUT,
        )

    return tool_name, tool_data


def parse_raw_data(data: str) -> Dict[str, Any]:
    """Parse the supplied JSON object."""
    if not data:
        raise ToolError(
            "No JSON data provided",
            EXIT_INVALID_INPUT,
        )

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ToolError(
            f"Invalid JSON data: {exc} (raw data: {data!r})",
            EXIT_INVALID_INPUT,
        ) from exc

    if not isinstance(parsed, dict):
        raise ToolError(
            f"Expected a JSON object, got {type(parsed).__name__}",
            EXIT_INVALID_INPUT,
        )

    return parsed


def setup_env(root_dir: str, tool_name: str) -> None:
    """Load environment variables and configure tool-specific paths."""
    load_env(os.path.join(root_dir, ".env"))

    cache_dir = os.path.join(
        root_dir,
        "cache",
        tool_name,
    )

    os.makedirs(cache_dir, exist_ok=True)

    tools_dir = os.path.join(root_dir, "tools")
    existing_pythonpath = os.environ.get("PYTHONPATH")

    pythonpath = tools_dir
    if existing_pythonpath:
        pythonpath += os.pathsep + existing_pythonpath

    os.environ.update(
        {
            "PYTHONPATH": pythonpath,
            "LLM_ROOT_DIR": root_dir,
            "LLM_TOOL_NAME": tool_name,
            "LLM_TOOL_CACHE_DIR": cache_dir,
        }
    )

    if "LLM_OUTPUT" not in os.environ:
        os.environ["LLM_OUTPUT"] = (
            "/dev/stdout" if os.name == "posix" else "CON"
        )


def load_env(file_path: str) -> None:
    """Load simple KEY=VALUE entries from an environment file."""
    try:
        with open(file_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()

                if (
                    not line
                    or line.startswith("#")
                    or "=" not in line
                ):
                    continue

                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()

                if (
                    len(val) >= 2
                    and val[0] == val[-1]
                    and val[0] in ('"', "'")
                ):
                    val = val[1:-1]

                if key and key not in os.environ:
                    os.environ[key] = val

    except (FileNotFoundError, PermissionError, OSError):
        # .env is optional.
        pass


def run(
    tool_name: str,
    tool_path: str,
    tool_func: str,
    tool_data: Dict[str, Any],
) -> None:
    """Import a tool module and invoke its requested function."""
    if not os.path.isfile(tool_path):
        raise ToolError(
            f"Tool file not found: {tool_path}",
            EXIT_FILE_NOT_FOUND,
        )

    try:
        spec = importlib.util.spec_from_file_location(
            os.path.basename(tool_path),
            tool_path,
        )

        if spec is None or spec.loader is None:
            raise ToolError(
                f"Unable to load tool module: {tool_path}",
                EXIT_FILE_NOT_FOUND,
            )

        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)

    except ToolError:
        raise

    except Exception as exc:
        raise ToolError(
            f"Failed to import tool module: {exc}",
            EXIT_FILE_NOT_FOUND,
        ) from exc

    if not hasattr(mod, tool_func):
        raise ToolError(
            f"No function '{tool_func}' in '{tool_path}'",
            EXIT_GENERAL_ERROR,
        )

    func = getattr(mod, tool_func)

    tool_data = _coerce_types(func, tool_data)
    tool_data = _filter_arguments(func, tool_data)

    try:
        value = func(**tool_data)

    except TypeError as exc:
        tb = sys.exc_info()[2]

        # If the traceback contains a frame beyond the function call,
        # the TypeError originated inside the tool rather than from
        # incorrect arguments supplied to the function.
        inside_tool = (
            tb is not None
            and tb.tb_next is not None
            and (
                tb.tb_next.tb_frame.f_code is not func.__code__
                or tb.tb_next.tb_next is not None
            )
        )

        if inside_tool:
            raise

        raise ToolError(
            f"Tool '{tool_name}' run() called with wrong arguments: {exc}",
            EXIT_INVALID_INPUT,
        ) from exc

    except ToolError:
        raise

    except Exception as exc:
        exit_code = getattr(exc, "exit_code", None)

        if isinstance(exit_code, int):
            raise ToolError(str(exc), exit_code) from exc

        raise

    return_to_llm(value)
    dump_result(tool_name)


def _filter_arguments(
    func: Any,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """Remove unexpected keyword arguments unless **kwargs is supported."""
    try:
        sig = inspect.signature(func)

        has_varkw = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in sig.parameters.values()
        )

        if has_varkw:
            return data

        valid_args = set(sig.parameters)
        return {
            key: value
            for key, value in data.items()
            if key in valid_args
        }

    except (TypeError, ValueError):
        return data


def _unwrap_optional(hint: Any) -> Any:
    """Return the non-None member of Optional/union-with-None types."""
    origin = get_origin(hint)

    if origin in (Union, types.UnionType):
        args = [
            arg
            for arg in get_args(hint)
            if arg is not type(None)
        ]

        if len(args) == 1:
            return args[0]

    return hint


def _coerce_types(
    func: Any,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """Coerce simple JSON values according to the function annotations."""
    try:
        hints = get_type_hints(func)

    except Exception:
        try:
            hints = {
                name: parameter.annotation
                for name, parameter in inspect.signature(
                    func
                ).parameters.items()
                if parameter.annotation is not inspect.Parameter.empty
            }

        except (TypeError, ValueError):
            return data

    coerced = data.copy()

    for key, value in coerced.items():
        if key not in hints:
            continue

        target_type = _unwrap_optional(hints[key])

        if get_origin(target_type) is Literal:
            allowed = get_args(target_type)

            if value not in allowed:
                for candidate in allowed:
                    try:
                        if type(candidate)(value) == candidate:
                            coerced[key] = candidate
                            break
                    except (TypeError, ValueError):
                        continue

            continue

        if not isinstance(value, (str, int, float, bool)):
            continue

        if target_type is bool:
            if isinstance(value, str):
                normalized = value.lower()

                if normalized in ("true", "1", "yes", "on"):
                    coerced[key] = True

                elif normalized in ("false", "0", "no", "off"):
                    coerced[key] = False

            elif not isinstance(value, bool):
                coerced[key] = bool(value)

        elif target_type is int:
            if not isinstance(value, bool):
                try:
                    coerced[key] = (
                        int(float(value))
                        if isinstance(value, str)
                        else int(value)
                    )
                except (TypeError, ValueError):
                    pass

        elif target_type is float:
            try:
                coerced[key] = float(value)
            except (TypeError, ValueError):
                pass

        elif target_type is str:
            coerced[key] = str(value)

    for key in ("timeout", "max_time", "connect_timeout"):
        if key in coerced:
            try:
                coerced[key] = int(coerced[key])
            except (TypeError, ValueError):
                pass

    return coerced


def return_to_llm(value: Any) -> None:
    """Write a tool result to the configured LLM output destination."""
    if value is None:
        return

    llm_output = os.environ.get("LLM_OUTPUT", "")

    use_file = bool(
        llm_output
        and llm_output not in (
            "/dev/stdout",
            "/dev/fd/1",
            "CON",
        )
    )

    writer = (
        open(llm_output, "w", encoding="utf-8")
        if use_file
        else sys.stdout
    )

    try:
        if isinstance(value, (str, int, float, bool)):
            writer.write(str(value))

        elif isinstance(value, (dict, list)):
            writer.write(json.dumps(value, indent=2))

    finally:
        if use_file:
            writer.close()


def dump_result(name: str) -> None:
    """Optionally echo saved results when debugging is enabled."""
    if (
        not os.getenv("LLM_DUMP_RESULTS")
        or not os.getenv("LLM_OUTPUT")
    ):
        return

    try:
        if not os.isatty(sys.stdout.fileno()):
            return
    except (AttributeError, OSError):
        return

    dump_pattern = os.environ.get("LLM_DUMP_RESULTS", "")

    try:
        if not re.search(rf"\b({dump_pattern})\b", name):
            return
    except re.error:
        return

    llm_output = os.environ.get("LLM_OUTPUT", "")

    if llm_output in (
        "/dev/stdout",
        "/dev/fd/1",
        "CON",
    ):
        return

    try:
        with open(llm_output, encoding="utf-8") as fh:
            print(
                "\x1b[2m----------------------\n"
                f"{fh.read()}\n"
                "----------------------\x1b[0m"
            )
    except (FileNotFoundError, PermissionError, OSError):
        pass


if __name__ == "__main__":
    main()
