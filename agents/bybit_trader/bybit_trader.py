#!/data/data/com.termux/files/usr/bin/python3
# ==============================================================================
# bybit_trader — Pyrmethus AIChat Tool Master Implementation
# Version 8.0.0
#
# Hardened Bybit V5 USDT-Perpetual implementation.
# Standard library only.
# AIChat JSON + CLI compatible.
# ==============================================================================

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import platform
import re
import signal
import sys
import threading
import time
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request
import uuid


__version__ = "8.0.0"

# ==============================================================================
# CONSTANTS
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130

BYBIT_MAINNET = "https://api.bybit.com"
BYBIT_TESTNET = "https://api-testnet.bybit.com"

VALID_TIMEFRAMES = {"1", "3", "5", "15", "60", "D"}

VALID_ACTIONS = {
    "ta",
    "scan",
    "calc_micro_profit",
    "wallet_balance",
    "positions",
    "open_orders",
    "orderbook",
    "price_action",
    "ticker",
    "instrument",
    "fills",
    "risk_status",
    "execute_scalp",
    "close_position",
    "cancel_orders",
    "set_leverage",
    "kill_switch",
    "history",
}

PUBLIC_ENDPOINTS = {
    "/v5/market/time",
    "/v5/market/kline",
    "/v5/market/orderbook",
    "/v5/market/instruments-info",
    "/v5/market/tickers",
}

DEFAULT_RECV_WINDOW = "5000"
DEFAULT_TIMEOUT = 10.0
MAX_RETRIES = 3

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]"
)

# ==============================================================================
# ERROR MODEL
# ==============================================================================

class ToolError(Exception):
    def __init__(
        self,
        message: str,
        exit_code: int = EXIT_ERROR,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": self.message,
            "exit_code": self.exit_code,
            **self.details,
        }


class ToolJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Decimal):
            return format(obj, "f")
        if isinstance(obj, dt.datetime):
            return obj.isoformat()
        if isinstance(obj, dt.date):
            return obj.isoformat()
        if isinstance(obj, set):
            return sorted(obj)
        return repr(obj)


# ==============================================================================
# GENERIC HELPERS
# ==============================================================================

def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def utc_day() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def positive_float(
    value: Any,
    name: str,
    allow_zero: bool = False,
) -> float:
    x = safe_float(value, float("nan"))

    if not math.isfinite(x):
        raise ToolError(
            f"Parameter '{name}' must be a finite number.",
            EXIT_INVALID_INPUT,
        )

    if allow_zero:
        if x < 0:
            raise ToolError(
                f"Parameter '{name}' cannot be negative.",
                EXIT_INVALID_INPUT,
            )
    elif x <= 0:
        raise ToolError(
            f"Parameter '{name}' must be greater than zero.",
            EXIT_INVALID_INPUT,
        )

    return x


def decimal(value: Any) -> Decimal:
    try:
        d = Decimal(str(value))
        if not d.is_finite():
            raise InvalidOperation
        return d
    except Exception as exc:
        raise ToolError(f"Invalid decimal value: {value}", EXIT_INVALID_INPUT) from exc


def quantize_step(
    value: Any,
    step: Any,
    rounding: str = ROUND_DOWN,
) -> str:
    v = decimal(value)
    s = decimal(step)

    if s <= 0:
        raise ToolError("Exchange precision step must be positive.")

    units = (v / s).quantize(Decimal("1"), rounding=rounding)
    result = units * s

    return format(result.normalize(), "f")


def clamp_decimal_qty(
    qty: Any,
    step: Any,
    minimum: Any,
    maximum: Any,
) -> Tuple[float, str]:
    q = decimal(qty)
    s = decimal(step)
    mn = decimal(minimum)
    mx = decimal(maximum)

    if q <= 0:
        raise ToolError("Quantity must be greater than zero.", EXIT_INVALID_INPUT)

    if mx > 0 and q > mx:
        q = mx

    q = (q / s).quantize(Decimal("1"), rounding=ROUND_DOWN) * s

    if q < mn:
        raise ToolError(
            f"Quantity {q} is below exchange minimum {mn}.",
            EXIT_INVALID_INPUT,
        )

    return float(q), format(q.normalize(), "f")


def validate_symbol(symbol: str) -> str:
    sym = str(symbol or "").strip().upper()

    if not sym:
        raise ToolError("Perpetual symbol cannot be empty.", EXIT_INVALID_INPUT)

    if not re.fullmatch(r"[A-Z0-9_-]{3,32}", sym):
        raise ToolError(
            f"Invalid perpetual symbol '{sym}'.",
            EXIT_INVALID_INPUT,
        )

    return sym


def validate_timeframe(timeframe: str) -> str:
    tf = str(timeframe or "1").strip().upper()

    if tf not in VALID_TIMEFRAMES:
        raise ToolError(
            f"Invalid timeframe '{tf}'. Allowed: {sorted(VALID_TIMEFRAMES)}",
            EXIT_INVALID_INPUT,
        )

    return tf


# ==============================================================================
# ENVIRONMENT / CONFIGURATION
# ==============================================================================

def get_data_dir() -> Path:
    path = Path("/data/data/com.termux/files/home/.config/aichat/functions/agents/bybit_trader/data").expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_env_config() -> Dict[str, str]:
    result: Dict[str, str] = {}

    candidates = [
        get_data_dir() / ".env",
        Path.home() / ".config" / "bybit-agent" / ".env",
    ]

    env_file = next((p for p in candidates if p.exists()), None)

    if env_file is None:
        return result

    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]

            result[key] = value
    except OSError:
        pass

    return result


def get_builtin_var(name: str) -> Optional[str]:
    return (
        os.environ.get(f"LLM_AGENT_VAR_{name}")
        or os.environ.get(f"LLM_AGENT_VAR_{name.lower()}")
        or os.environ.get(f"LLM_AGENT_VAR_{name.upper()}")
    )


def get_agent_var(name: str, default: str = "") -> str:
    return get_builtin_var(name) or default


def cfg_value(
    agent_var: str,
    env_var: str,
    default: str,
) -> str:
    cfg = get_env_config()

    return (
        os.environ.get(agent_var)
        or os.environ.get(env_var)
        or cfg.get(env_var)
        or default
    )


def get_execution_context() -> dict[str, Any]:
    return {
        "tool_name": os.environ.get(
            "LLM_TOOL_NAME",
            "bybit_trader",
        ),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "utc_now": utc_now(),
    }


# ==============================================================================
# LOGGING / UI
# ==============================================================================

def _is_tty(no_color: bool = False) -> bool:
    if no_color or os.environ.get("NO_COLOR"):
        return False

    return (
        sys.stderr.isatty()
        and os.environ.get("TERM", "").lower() not in {"", "dumb"}
    )


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _cprint(
    text: str,
    file: Any = None,
    no_color: bool = False,
    end: str = "\n",
) -> None:
    target = file or sys.stderr

    if not _is_tty(no_color):
        text = _strip_ansi(text)

    print(text, file=target, flush=True, end=end)


def print_human_readable_ui(
    data: dict[str, Any],
    no_color: bool = False,
) -> None:
    if not _is_tty(no_color):
        return

    success = bool(data.get("success"))
    color = NEON_GREEN if success else NEON_RED
    symbol = "✓" if success else "✗"

    border = "─" * 64

    _cprint(
        f"{NEON_PURPLE}╭{border}╮{RESET}",
        no_color=no_color,
    )

    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_PINK}⚡ [BYBIT AGENT v{__version__}]{RESET} "
        f"{color}{BOLD}{symbol} "
        f"{'SUCCESS' if success else 'FAILED'}{RESET}",
        no_color=no_color,
    )

    _cprint(
        f"{NEON_PURPLE}├{border}┤{RESET}",
        no_color=no_color,
    )

    for key in (
        "action",
        "symbol",
        "mode",
        "risk_gate_pass",
        "order_id",
        "duration_ms",
    ):
        if key in data:
            _cprint(
                f"{NEON_PURPLE}│{RESET} "
                f"{NEON_CYAN}{key}:{RESET} "
                f"{data[key]}",
                no_color=no_color,
            )

    if not success and data.get("error"):
        _cprint(
            f"{NEON_PURPLE}├{border}┤{RESET}",
            no_color=no_color,
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET} "
            f"{NEON_RED}Error:{RESET} "
            f"{data['error']}",
            no_color=no_color,
        )

    _cprint(
        f"{NEON_PURPLE}╰{border}╯{RESET}",
        no_color=no_color,
    )


# ==============================================================================
# STATE / LOGGING
# ==============================================================================

def state_file() -> Path:
    return get_data_dir() / "state.json"


def trade_log_file() -> Path:
    return get_data_dir() / "trade_log.jsonl"


def load_state() -> Dict[str, Any]:
    default = {
        "day": utc_day(),
        "daily_realized_pnl": 0.0,
        "last_execution_ts": 0.0,
        "execution_attempts": 0,
        "kill_switch": False,
        "last_order_id": None,
        "last_order_link_id": None,
    }

    path = state_file()

    if not path.exists():
        return default

    try:
        data = json.loads(path.read_text(encoding="utf-8"))

        if not isinstance(data, dict):
            return default

    except Exception:
        return default

    if data.get("day") != utc_day():
        return default

    merged = dict(default)
    merged.update(data)

    return merged


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.tmp"
    )

    try:
        tmp.write_text(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
                cls=ToolJSONEncoder,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def save_state(state: Dict[str, Any]) -> None:
    atomic_write_json(state_file(), state)


def append_log(record: Dict[str, Any]) -> None:
    safe = dict(record)

    for key in (
        "api_key",
        "api_secret",
        "secret",
        "authorization",
    ):
        if key in safe:
            safe[key] = "***REDACTED***"

    path = trade_log_file()

    with path.open(
        "a",
        encoding="utf-8",
    ) as fp:
        fp.write(
            json.dumps(
                safe,
                cls=ToolJSONEncoder,
                ensure_ascii=False,
            )
            + "\n"
        )


def kill_switch_active() -> bool:
    cfg = get_env_config()

    return parse_bool(
        os.environ.get(
            "BYBIT_KILL_SWITCH",
            cfg.get("KILL_SWITCH", "false"),
        ),
        False,
    ) or bool(load_state().get("kill_switch"))


def set_kill_switch(active: bool) -> Dict[str, Any]:
    state = load_state()
    state["kill_switch"] = bool(active)
    state["kill_switch_changed_at"] = utc_now()
    save_state(state)

    return {
        "success": True,
        "kill_switch": bool(active),
        "timestamp": utc_now(),
    }


# ==============================================================================
# CACHE
# ==============================================================================

def build_cache_key(prefix: str, *args: Any) -> str:
    payload = "|".join(
        [
            __version__,
            prefix,
            *(str(a) for a in args),
        ]
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


class ToolCache:
    def __init__(
        self,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.cache_dir = cache_dir or (
            get_data_dir() / "cache"
        )
        self.cache_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    def get(
        self,
        key_str: str,
        ttl_seconds: int = 5,
    ) -> Optional[dict[str, Any]]:
        path = self.cache_dir / f"{key_str}.json"

        if not path.exists():
            return None

        try:
            age = time.time() - path.stat().st_mtime

            if age > ttl_seconds:
                path.unlink(missing_ok=True)
                return None

            data = json.loads(
                path.read_text(encoding="utf-8")
            )

            return data if isinstance(data, dict) else None

        except Exception:
            return None

    def set(
        self,
        key_str: str,
        value: Any,
    ) -> None:
        path = self.cache_dir / f"{key_str}.json"

        try:
            atomic_write_json(path, value)
        except Exception:
            pass


def invalidate_cache(
    cache: Optional[ToolCache] = None,
) -> int:
    c = cache or ToolCache()
    removed = 0

    for path in c.cache_dir.glob("*.json"):
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass

    return removed


# ==============================================================================
# SIGNAL HANDLING
# ==============================================================================

class GracefulShutdown:
    def __init__(self) -> None:
        self.interrupted = False
        self.old_int = None
        self.old_term = None

    def __enter__(self):
        if threading.current_thread() is threading.main_thread():
            try:
                self.old_int = signal.signal(
                    signal.SIGINT,
                    self._handler,
                )
                self.old_term = signal.signal(
                    signal.SIGTERM,
                    self._handler,
                )
            except (ValueError, AttributeError):
                pass

        return self

    def __exit__(self, *args):
        if threading.current_thread() is threading.main_thread():
            if self.old_int is not None:
                signal.signal(
                    signal.SIGINT,
                    self.old_int,
                )

            if self.old_term is not None:
                signal.signal(
                    signal.SIGTERM,
                    self.old_term,
                )

    def _handler(self, signum, frame):
        self.interrupted = True

    def should_stop(self) -> bool:
        return self.interrupted


# ==============================================================================
# LIVE AUTHORIZATION
# ==============================================================================

def live_authorized(
    testnet: bool,
    requested_live: bool,
    confirmation: str,
) -> Tuple[bool, str]:
    if not requested_live:
        return False, "DRY_RUN_REQUESTED"

    if testnet:
        return False, "TESTNET_MODE_ACTIVE"

    if not parse_bool(
        cfg_value(
            "LLM_AGENT_VAR_LIVE_TRADING_ENABLED",
            "LIVE_TRADING_ENABLED",
            "false",
        )
    ):
        return False, "LIVE_TRADING_ENABLED_FALSE"

    if not parse_bool(
        cfg_value(
            "ALLOW_LIVE_TRADING",
            "ALLOW_LIVE_TRADING",
            "false",
        )
    ):
        return False, "ALLOW_LIVE_TRADING_FALSE"

    if kill_switch_active():
        return False, "KILL_SWITCH_ACTIVE"

    expected = cfg_value(
        "LIVE_CONFIRMATION_TOKEN",
        "LIVE_CONFIRMATION_TOKEN",
        "I_UNDERSTAND_LIVE_RISK",
    )

    if confirmation != expected:
        return False, "LIVE_CONFIRMATION_TOKEN_MISMATCH"

    return True, "AUTHORIZED_LIVE"


# ==============================================================================
# BYBIT V5 HTTP CLIENT
# ==============================================================================

class BybitV5:
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
    ):
        cfg = get_env_config()

        self.api_key = (
            api_key
            or os.environ.get("BYBIT_API_KEY")
            or cfg.get("BYBIT_API_KEY", "")
        ).strip()

        self.api_secret = (
            api_secret
            or os.environ.get("BYBIT_API_SECRET")
            or cfg.get("BYBIT_API_SECRET", "")
        ).strip()

        self.testnet = testnet
        self.base_url = (
            BYBIT_TESTNET
            if testnet
            else BYBIT_MAINNET
        )

        self.time_offset_ms = 0
        self.last_request_ms = 0

        self.timeout = safe_float(
            cfg_value(
                "BYBIT_HTTP_TIMEOUT",
                "BYBIT_HTTP_TIMEOUT",
                str(DEFAULT_TIMEOUT),
            ),
            DEFAULT_TIMEOUT,
        )

        self.sync_time()

    @property
    def has_credentials(self) -> bool:
        return bool(
            self.api_key
            and self.api_secret
        )

    def sync_time(self) -> None:
        try:
            t0 = int(time.time() * 1000)

            req = urllib.request.Request(
                f"{self.base_url}/v5/market/time",
                headers={
                    "User-Agent":
                        "AIChat-BybitAgent/8.0",
                },
            )

            with urllib.request.urlopen(
                req,
                timeout=3,
            ) as response:
                t1 = int(time.time() * 1000)

                payload = json.loads(
                    response.read().decode("utf-8")
                )

            result = payload.get("result", {})

            server_ms = (
                safe_float(
                    result.get("timeNano", 0)
                )
                / 1_000_000
            )

            if server_ms <= 0:
                server_ms = safe_float(
                    payload.get("time", 0)
                )

            if server_ms > 0:
                local_midpoint = (
                    t0 + t1
                ) / 2.0

                self.time_offset_ms = int(
                    server_ms - local_midpoint
                )

        except Exception:
            self.time_offset_ms = 0

    def _sign(
        self,
        timestamp: str,
        recv_window: str,
        payload: str,
    ) -> str:
        raw = (
            timestamp
            + self.api_key
            + recv_window
            + payload
        )

        return hmac.new(
            self.api_secret.encode("utf-8"),
            raw.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _retryable_http(exc: Exception) -> bool:
        if isinstance(exc, urllib.error.HTTPError):
            return exc.code in {
                408,
                429,
                500,
                502,
                503,
                504,
            }

        if isinstance(
            exc,
            (
                urllib.error.URLError,
                TimeoutError,
            ),
        ):
            return True

        return False

    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        method = method.upper()
        public = endpoint in PUBLIC_ENDPOINTS

        if not public and not self.has_credentials:
            raise ToolError(
                "Private endpoint requires "
                "BYBIT_API_KEY and BYBIT_API_SECRET.",
                EXIT_PERMISSION_DENIED,
            )

        clean_params = {
            str(k): (
                "true"
                if v is True
                else "false"
                if v is False
                else str(v)
            )
            for k, v in (params or {}).items()
            if v is not None
        }

        query_str = urllib.parse.urlencode(
            sorted(clean_params.items())
        )

        url = (
            f"{self.base_url}{endpoint}"
        )

        if method == "GET" and query_str:
            url += "?" + query_str

        payload_for_sign = query_str
        body_bytes = None

        if method != "GET":
            payload_for_sign = json.dumps(
                data or {},
                separators=(",", ":"),
                ensure_ascii=False,
            )

            body_bytes = payload_for_sign.encode(
                "utf-8"
            )

        last_error: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            timestamp = str(
                int(time.time() * 1000)
                + self.time_offset_ms
            )

            headers = {
                "Content-Type":
                    "application/json",
                "User-Agent":
                    "AIChat-BybitAgent/8.0",
            }

            if not public:
                headers.update({
                    "X-BAPI-API-KEY":
                        self.api_key,
                    "X-BAPI-TIMESTAMP":
                        timestamp,
                    "X-BAPI-RECV-WINDOW":
                        DEFAULT_RECV_WINDOW,
                    "X-BAPI-SIGN":
                        self._sign(
                            timestamp,
                            DEFAULT_RECV_WINDOW,
                            payload_for_sign,
                        ),
                })

            request = urllib.request.Request(
                url,
                data=body_bytes,
                headers=headers,
                method=method,
            )

            started = time.monotonic()

            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout,
                ) as response:
                    raw = response.read().decode(
                        "utf-8"
                    )

                self.last_request_ms = int(
                    (time.monotonic() - started)
                    * 1000
                )

                result = json.loads(raw)

                ret_code = result.get(
                    "retCode",
                    0,
                )

                if ret_code != 0:
                    ret_msg = str(
                        result.get(
                            "retMsg",
                            "Unknown Bybit error",
                        )
                    )

                    lowered = ret_msg.lower()

                    if (
                        ret_code == 10002
                        or "timestamp" in lowered
                    ) and attempt == 0:
                        self.sync_time()
                        continue

                    if (
                        ret_code in {
                            10006,
                            10016,
                        }
                        or "rate limit" in lowered
                    ) and attempt < MAX_RETRIES - 1:
                        time.sleep(
                            min(
                                0.5 * (
                                    2 ** attempt
                                ),
                                4.0,
                            )
                        )
                        continue

                    raise ToolError(
                        f"Bybit API Error "
                        f"[{ret_code}]: {ret_msg}",
                        EXIT_ERROR,
                    )

                return result.get(
                    "result",
                    {},
                )

            except urllib.error.HTTPError as exc:
                last_error = exc

                if not self._retryable_http(exc):
                    raise ToolError(
                        f"HTTP {exc.code} from Bybit.",
                        EXIT_ERROR,
                    )

            except Exception as exc:
                last_error = exc

                if isinstance(
                    exc,
                    ToolError,
                ):
                    raise

                if not self._retryable_http(exc):
                    raise

            if attempt < MAX_RETRIES - 1:
                time.sleep(
                    min(
                        0.25 * (2 ** attempt),
                        2.0,
                    )
                )

        raise ToolError(
            f"Bybit request to '{endpoint}' "
            f"failed after {MAX_RETRIES} attempts: "
            f"{last_error}",
            EXIT_ERROR,
        )


# ==============================================================================
# EXCHANGE METADATA
# ==============================================================================

def fetch_instrument(
    client: BybitV5,
    symbol: str,
) -> Dict[str, Any]:
    sym = validate_symbol(symbol)

    result = client.request(
        "GET",
        "/v5/market/instruments-info",
        {
            "category": "linear",
            "symbol": sym,
        },
    )

    for item in result.get("list", []):
        if item.get("symbol") == sym:
            return item

    raise ToolError(
        f"Linear instrument metadata not found "
        f"for '{sym}'.",
        EXIT_FILE_NOT_FOUND,
    )


def fetch_precision(
    client: BybitV5,
    symbol: str,
) -> Tuple[str, str, str, str]:
    inst = fetch_instrument(
        client,
        symbol,
    )

    price_filter = inst.get(
        "priceFilter",
        {},
    )

    lot_filter = inst.get(
        "lotSizeFilter",
        {},
    )

    return (
        str(
            price_filter.get(
                "tickSize",
                "0.01",
            )
        ),
        str(
            lot_filter.get(
                "qtyStep",
                "0.01",
            )
        ),
        str(
            lot_filter.get(
                "minOrderQty",
                "0.01",
            )
        ),
        str(
            lot_filter.get(
                "maxOrderQty",
                "0",
            )
        ),
    )


# ==============================================================================
# MARKET DATA
# ==============================================================================

def get_orderbook(
    client: BybitV5,
    symbol: str,
) -> Dict[str, Any]:
    sym = validate_symbol(symbol)

    result = client.request(
        "GET",
        "/v5/market/orderbook",
        {
            "category": "linear",
            "symbol": sym,
            "limit": "25",
        },
    )

    bids = [
        x for x in result.get("b", [])
        if isinstance(x, list) and len(x) >= 2
    ]

    asks = [
        x for x in result.get("a", [])
        if isinstance(x, list) and len(x) >= 2
    ]

    bids.sort(
        key=lambda x: safe_float(x[0]),
        reverse=True,
    )

    asks.sort(
        key=lambda x: safe_float(x[0])
    )

    if not bids or not asks:
        raise ToolError(
            f"Orderbook for {sym} is empty."
        )

    best_bid = safe_float(bids[0][0])
    best_ask = safe_float(asks[0][0])

    if (
        best_bid <= 0
        or best_ask <= 0
        or best_ask < best_bid
    ):
        raise ToolError(
            "Invalid bid/ask spread state."
        )

    spread = best_ask - best_bid

    return {
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": (best_bid + best_ask) / 2.0,
        "spread": spread,
        "spread_pct": (
            spread / best_bid * 100.0
        ),
        "timestamp": utc_now(),
    }


def get_ticker(
    client: BybitV5,
    symbol: str,
) -> Dict[str, Any]:
    sym = validate_symbol(symbol)

    result = client.request(
        "GET",
        "/v5/market/tickers",
        {
            "category": "linear",
            "symbol": sym,
        },
    )

    rows = result.get("list", [])

    if not rows:
        raise ToolError(
            f"No ticker data for {sym}."
        )

    return rows[0]


def normalize_candles(
    raw: List[Any],
    drop_latest: bool = True,
) -> List[List[Any]]:
    valid: List[List[Any]] = []

    for candle in raw:
        if not (
            isinstance(candle, (list, tuple))
            and len(candle) >= 7
        ):
            continue

        try:
            int(candle[0])

            for index in range(1, 7):
                float(candle[index])

            valid.append(list(candle))

        except (TypeError, ValueError):
            continue

    valid.sort(
        key=lambda x: int(x[0])
    )

    if (
        drop_latest
        and len(valid) > 1
    ):
        return valid[:-1]

    return valid


def get_closed_klines(
    client: BybitV5,
    symbol: str,
    timeframe: str,
    limit: int = 200,
) -> List[List[Any]]:
    result = client.request(
        "GET",
        "/v5/market/kline",
        {
            "category": "linear",
            "symbol": validate_symbol(symbol),
            "interval": validate_timeframe(timeframe),
            "limit": str(
                min(max(limit, 1), 1000)
            ),
        },
    )

    return normalize_candles(
        result.get("list", []),
        True,
    )


# ==============================================================================
# TECHNICAL ANALYSIS
# ==============================================================================

def ema_series(
    values: List[float],
    period: int,
) -> List[float]:
    if not values:
        return []

    if len(values) < period:
        return [values[-1]] * len(values)

    multiplier = 2.0 / (
        period + 1.0
    )

    seed = sum(
        values[:period]
    ) / period

    result = [seed]
    current = seed

    for value in values[period:]:
        current = (
            value * multiplier
            + current
            * (1.0 - multiplier)
        )

        result.append(current)

    return (
        [seed] * (period - 1)
        + result
    )


def rsi(
    closes: List[float],
    period: int = 14,
) -> float:
    if len(closes) <= period:
        return 50.0

    changes = [
        closes[i]
        - closes[i - 1]
        for i in range(1, len(closes))
    ]

    gains = [
        max(x, 0.0)
        for x in changes
    ]

    losses = [
        max(-x, 0.0)
        for x in changes
    ]

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for index in range(
        period,
        len(changes),
    ):
        avg_gain = (
            avg_gain * (period - 1)
            + gains[index]
        ) / period

        avg_loss = (
            avg_loss * (period - 1)
            + losses[index]
        ) / period

    if avg_loss <= 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100.0 - (
        100.0 / (1.0 + rs)
    )


def macd(
    closes: List[float],
) -> Dict[str, float]:
    if len(closes) < 35:
        return {
            "macd": 0.0,
            "signal": 0.0,
            "histogram": 0.0,
        }

    fast = ema_series(
        closes,
        12,
    )

    slow = ema_series(
        closes,
        26,
    )

    difference = [
        fast[i] - slow[i]
        for i in range(len(closes))
    ]

    signal = ema_series(
        difference,
        9,
    )

    current = difference[-1]
    sig = signal[-1]

    return {
        "macd": round(current, 8),
        "signal": round(sig, 8),
        "histogram": round(
            current - sig,
            8,
        ),
    }


def bollinger(
    closes: List[float],
    period: int = 20,
) -> Dict[str, float]:
    if len(closes) < period:
        price = (
            closes[-1]
            if closes
            else 0.0
        )

        return {
            "upper": price,
            "middle": price,
            "lower": price,
            "bandwidth_pct": 0.0,
            "percent_b": 0.5,
        }

    sample = closes[-period:]

    mean = sum(sample) / period

    variance = (
        sum(
            (x - mean) ** 2
            for x in sample
        )
        / period
    )

    deviation = math.sqrt(
        max(variance, 0.0)
    )

    upper = mean + 2.0 * deviation
    lower = mean - 2.0 * deviation

    width = upper - lower
    price = closes[-1]

    return {
        "upper": round(
            upper,
            8,
        ),
        "middle": round(
            mean,
            8,
        ),
        "lower": round(
            lower,
            8,
        ),
        "bandwidth_pct": round(
            (
                width / mean * 100.0
                if mean > 0
                else 0.0
            ),
            5,
        ),
        "percent_b": round(
            (
                (price - lower) / width
                if width > 0
                else 0.5
            ),
            5,
        ),
    }


def atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
) -> float:
    if len(closes) <= period:
        return 0.0

    trs = []

    for i in range(
        1,
        len(closes),
    ):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(
                    highs[i]
                    - closes[i - 1]
                ),
                abs(
                    lows[i]
                    - closes[i - 1]
                ),
            )
        )

    if len(trs) < period:
        return 0.0

    current = (
        sum(trs[:period])
        / period
    )

    for tr in trs[period:]:
        current = (
            current * (period - 1)
            + tr
        ) / period

    return current


def price_action(
    raw: List[Any],
) -> Dict[str, Any]:
    candles = normalize_candles(
        raw,
        True,
    )

    if len(candles) < 3:
        return {
            "pattern": "NONE",
            "bullish": False,
            "bearish": False,
        }

    current = candles[-1]
    previous = candles[-2]

    co = safe_float(current[1])
    ch = safe_float(current[2])
    cl = safe_float(current[3])
    cc = safe_float(current[4])

    po = safe_float(previous[1])
    pc = safe_float(previous[4])

    body = abs(cc - co)
    rng = max(ch - cl, 0.0)

    lower_wick = (
        min(co, cc) - cl
    )

    upper_wick = (
        ch - max(co, cc)
    )

    reference = max(
        body,
        rng * 0.02,
    )

    bull_pin = (
        rng > 0
        and lower_wick >= 2.0 * reference
        and cc >= co
    )

    bear_pin = (
        rng > 0
        and upper_wick >= 2.0 * reference
        and cc <= co
    )

    bull_engulf = (
        cc > co
        and pc < po
        and cc >= po
        and co <= pc
    )

    bear_engulf = (
        cc < co
        and pc > po
        and cc <= po
        and co >= pc
    )

    if bull_engulf:
        pattern = "BULL_ENGULF"
    elif bear_engulf:
        pattern = "BEAR_ENGULF"
    elif bull_pin:
        pattern = "BULL_PIN"
    elif bear_pin:
        pattern = "BEAR_PIN"
    else:
        pattern = "NONE"

    return {
        "pattern": pattern,
        "bullish": (
            bull_pin
            or bull_engulf
        ),
        "bearish": (
            bear_pin
            or bear_engulf
        ),
    }


# ==============================================================================
# ACCOUNT / POSITION DATA
# ==============================================================================

def get_wallet(
    client: BybitV5,
) -> Dict[str, Any]:
    return client.request(
        "GET",
        "/v5/account/wallet-balance",
        {
            "accountType": "UNIFIED",
            "coin": "USDT",
        },
    )


def get_positions(
    client: BybitV5,
    symbol: Optional[str] = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "category": "linear",
        "settleCoin": "USDT",
    }

    if symbol:
        params["symbol"] = validate_symbol(
            symbol
        )

    result = client.request(
        "GET",
        "/v5/position/list",
        params,
    )

    return result.get(
        "list",
        [],
    )


def get_open_orders(
    client: BybitV5,
    symbol: Optional[str] = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "category": "linear",
        "settleCoin": "USDT",
    }

    if symbol:
        params["symbol"] = validate_symbol(
            symbol
        )

    result = client.request(
        "GET",
        "/v5/order/realtime",
        params,
    )

    return result.get(
        "list",
        [],
    )


def get_fills(
    client: BybitV5,
    symbol: Optional[str] = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "category": "linear",
        "limit": "50",
    }

    if symbol:
        params["symbol"] = validate_symbol(
            symbol
        )

    result = client.request(
        "GET",
        "/v5/execution/list",
        params,
    )

    return result.get(
        "list",
        [],
    )


# ==============================================================================
# TA HANDLER
# ==============================================================================

def handle_ta(
    client: BybitV5,
    symbol: str,
    timeframe: str,
) -> Dict[str, Any]:
    sym = validate_symbol(symbol)
    tf = validate_timeframe(timeframe)

    candles = get_closed_klines(
        client,
        sym,
        tf,
        200,
    )

    if len(candles) < 60:
        raise ToolError(
            f"Insufficient candle history: "
            f"{len(candles)}; minimum 60 required."
        )

    closes = [
        safe_float(c[4])
        for c in candles
    ]

    highs = [
        safe_float(c[2])
        for c in candles
    ]

    lows = [
        safe_float(c[3])
        for c in candles
    ]

    price = closes[-1]

    rsi_value = rsi(
        closes,
        14,
    )

    macd_value = macd(
        closes
    )

    bb = bollinger(
        closes
    )

    ema9 = ema_series(
        closes,
        9,
    )[-1]

    ema21 = ema_series(
        closes,
        21,
    )[-1]

    ema50 = ema_series(
        closes,
        50,
    )[-1]

    atr_value = atr(
        highs,
        lows,
        closes,
        14,
    )

    if (
        ema9 > ema21 > ema50
        and price > ema9
    ):
        trend = "STRONG_BULLISH"
    elif (
        ema9 > ema21
        and price > ema21
    ):
        trend = "BULLISH"
    elif (
        ema9 < ema21 < ema50
        and price < ema9
    ):
        trend = "STRONG_BEARISH"
    elif (
        ema9 < ema21
        and price < ema21
    ):
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    return {
        "success": True,
        "symbol": sym,
        "timeframe": (
            tf + "m"
            if tf.isdigit()
            else tf
        ),
        "current_price": price,
        "trend": trend,
        "rsi_14": round(
            rsi_value,
            2,
        ),
        "rsi_state": (
            "OVERSOLD"
            if rsi_value <= 30
            else "OVERBOUGHT"
            if rsi_value >= 70
            else "NEUTRAL"
        ),
        "macd": macd_value,
        "macd_bias": (
            "BULLISH"
            if macd_value["histogram"] > 0
            else "BEARISH"
            if macd_value["histogram"] < 0
            else "NEUTRAL"
        ),
        "bollinger_bands": bb,
        "ema": {
            "ema_9": round(
                ema9,
                8,
            ),
            "ema_21": round(
                ema21,
                8,
            ),
            "ema_50": round(
                ema50,
                8,
            ),
        },
        "atr_14": round(
            atr_value,
            8,
        ),
        "timestamp": utc_now(),
    }


# ==============================================================================
# SCAN / SIGNAL ENGINE
# ==============================================================================

def handle_scan(
    client: BybitV5,
    symbol: str,
    max_spread: float,
    target_profit: float,
    leverage: int,
    max_notional: float,
    maker_fee_pct: float,
    taker_fee_pct: float,
) -> Dict[str, Any]:
    sym = validate_symbol(symbol)

    max_spread = positive_float(
        max_spread,
        "max_spread_pct",
        allow_zero=True,
    )

    target_profit = positive_float(
        target_profit,
        "target_profit_usdt",
    )

    leverage = int(
        positive_float(
            leverage,
            "leverage",
        )
    )

    max_notional = positive_float(
        max_notional,
        "max_notional_usdt",
    )

    orderbook = get_orderbook(
        client,
        sym,
    )

    candles = get_closed_klines(
        client,
        sym,
        "1",
        100,
    )

    ta = handle_ta(
        client,
        sym,
        "1",
    )

    bid_volume = sum(
        safe_float(row[1])
        for row in orderbook["bids"][:10]
    )

    ask_volume = sum(
        safe_float(row[1])
        for row in orderbook["asks"][:10]
    )

    imbalance = (
        bid_volume / ask_volume
        if ask_volume > 0
        else 1.0
    )

    pa = price_action(
        candles
    )

    trend = ta["trend"]

    score_long = 0
    score_short = 0

    if orderbook["spread_pct"] <= max_spread:
        score_long += 1
        score_short += 1

    if imbalance >= 1.50:
        score_long += 2
    elif imbalance <= 0.67:
        score_short += 2

    if pa["bullish"]:
        score_long += 2

    if pa["bearish"]:
        score_short += 2

    if trend in {
        "BULLISH",
        "STRONG_BULLISH",
    }:
        score_long += 2

    if trend in {
        "BEARISH",
        "STRONG_BEARISH",
    }:
        score_short += 2

    rsi_value = safe_float(
        ta["rsi_14"]
    )

    if 45 <= rsi_value <= 68:
        score_long += 1

    if 32 <= rsi_value <= 55:
        score_short += 1

    if (
        ta["macd_bias"]
        == "BULLISH"
    ):
        score_long += 1

    if (
        ta["macd_bias"]
        == "BEARISH"
    ):
        score_short += 1

    if (
        score_long >= 5
        and score_long > score_short
    ):
        bias = "LONG"
    elif (
        score_short >= 5
        and score_short > score_long
    ):
        bias = "SHORT"
    else:
        bias = "NEUTRAL"

    entry = (
        orderbook["best_bid"]
        if bias == "LONG"
        else orderbook["best_ask"]
        if bias == "SHORT"
        else orderbook["mid"]
    )

    tick, step, min_qty, max_qty = (
        fetch_precision(
            client,
            sym,
        )
    )

    atr_value = safe_float(
        ta["atr_14"]
    )

    maker_round_trip = (
        maker_fee_pct * 2.0 / 100.0
    )

    taker_round_trip = (
        taker_fee_pct * 2.0 / 100.0
    )

    selected_fee = (
        maker_round_trip
        if maker_fee_pct <= taker_fee_pct
        else taker_round_trip
    )

    fee_usdt = (
        entry
        * selected_fee
    )

    min_move = (
        target_profit
        + fee_usdt
    )

    volatility_move = max(
        atr_value * 0.20,
        entry * 0.0005,
    )

    required_delta = max(
        min_move,
        volatility_move,
    )

    raw_qty = min(
        max_notional / entry,
        (
            target_profit
            + fee_usdt
        ) / required_delta,
    )

    final_qty, qty_string = (
        clamp_decimal_qty(
            raw_qty,
            step,
            min_qty,
            max_qty,
        )
    )

    notional = (
        final_qty * entry
    )

    estimated_fees = (
        notional
        * selected_fee
    )

    stop_distance = max(
        atr_value * 0.75,
        orderbook["spread"] * 3.0,
        entry * 0.001,
    )

    reward_risk = (
        required_delta
        / stop_distance
        if stop_distance > 0
        else 0.0
    )

    min_rr = safe_float(
        cfg_value(
            "LLM_AGENT_VAR_MIN_REWARD_RISK",
            "MIN_REWARD_RISK",
            "1.25",
        ),
        1.25,
    )

    spread_pass = (
        orderbook["spread_pct"]
        <= max_spread
    )

    notional_pass = (
        notional
        <= max_notional
    )

    rr_pass = (
        reward_risk >= min_rr
    )

    confidence = round(
        max(
            score_long,
            score_short,
        )
        / 10.0,
        3,
    )

    return {
        "success": True,
        "symbol": sym,
        "best_bid": orderbook["best_bid"],
        "best_ask": orderbook["best_ask"],
        "mid": orderbook["mid"],
        "spread_pct": round(
            orderbook["spread_pct"],
            6,
        ),
        "spread_pass": spread_pass,
        "imbalance_ratio": round(
            imbalance,
            4,
        ),
        "long_score": score_long,
        "short_score": score_short,
        "confidence": confidence,
        "pattern_detected": pa["pattern"],
        "bias": bias,
        "ta_summary": {
            "trend": trend,
            "rsi_14": ta["rsi_14"],
            "macd_bias": ta["macd_bias"],
            "atr_14": atr_value,
        },
        "micro_profit_plan": {
            "target_profit_usdt":
                target_profit,
            "calculated_qty":
                final_qty,
            "calculated_qty_formatted":
                qty_string,
            "estimated_notional":
                round(
                    notional,
                    8,
                ),
            "estimated_fees":
                round(
                    estimated_fees,
                    8,
                ),
            "required_price_delta":
                round(
                    required_delta,
                    8,
                ),
            "stop_distance":
                round(
                    stop_distance,
                    8,
                ),
            "reward_risk":
                round(
                    reward_risk,
                    4,
                ),
            "reward_risk_pass":
                rr_pass,
        },
        "risk_gate_pass": (
            bias != "NEUTRAL"
            and spread_pass
            and notional_pass
            and rr_pass
        ),
        "timestamp": utc_now(),
    }


# ==============================================================================
# RISK ENGINE
# ==============================================================================

def wallet_equity_usdt(
    wallet: Dict[str, Any],
) -> float:
    rows = wallet.get(
        "coin",
        [],
    )

    for row in rows:
        if str(
            row.get("coin", "")
        ).upper() == "USDT":
            for key in (
                "totalEquity",
                "walletBalance",
                "equity",
            ):
                value = safe_float(
                    row.get(key),
                    -1,
                )

                if value >= 0:
                    return value

    for key in (
        "totalEquity",
        "totalWalletBalance",
        "totalAvailableBalance",
    ):
        value = safe_float(
            wallet.get(key),
            -1,
        )

        if value >= 0:
            return value

    return 0.0


def position_is_open(
    position: Dict[str, Any],
) -> bool:
    size = safe_float(
        position.get("size"),
        0,
    )

    return abs(size) > 0


def risk_status(
    client: BybitV5,
    symbol: str,
    max_notional: float,
    max_open_positions: int,
    max_daily_loss: float,
) -> Dict[str, Any]:
    state = load_state()

    wallet = get_wallet(
        client
    )

    positions = get_positions(
        client
    )

    open_positions = [
        p
        for p in positions
        if position_is_open(p)
    ]

    equity = wallet_equity_usdt(
        wallet
    )

    daily_pnl = safe_float(
        state.get(
            "daily_realized_pnl",
            0,
        )
    )

    loss_limit_hit = (
        daily_pnl <= -abs(
            max_daily_loss
        )
    )

    position_limit_hit = (
        len(open_positions)
        >= max_open_positions
    )

    return {
        "success": True,
        "equity_usdt": round(
            equity,
            8,
        ),
        "daily_realized_pnl": round(
            daily_pnl,
            8,
        ),
        "daily_loss_limit_usdt":
            max_daily_loss,
        "daily_loss_limit_hit":
            loss_limit_hit,
        "open_position_count":
            len(open_positions),
        "max_open_positions":
            max_open_positions,
        "position_limit_hit":
            position_limit_hit,
        "max_notional_usdt":
            max_notional,
        "kill_switch":
            kill_switch_active(),
        "live_environment":
            not client.testnet,
        "timestamp": utc_now(),
    }


def enforce_entry_risk(
    client: BybitV5,
    symbol: str,
    notional: float,
    stop_distance: float,
    entry_price: float,
    max_notional: float,
    max_account_risk_pct: float,
    max_daily_loss: float,
    max_open_positions: int,
) -> Dict[str, Any]:
    if kill_switch_active():
        raise ToolError(
            "Execution blocked: kill switch active.",
            EXIT_PERMISSION_DENIED,
        )

    if notional > max_notional:
        raise ToolError(
            f"Notional {notional:.8f} exceeds "
            f"maximum {max_notional:.8f}.",
            EXIT_PERMISSION_DENIED,
        )

    positions = get_positions(
        client
    )

    open_positions = [
        p
        for p in positions
        if position_is_open(p)
    ]

    if len(open_positions) >= max_open_positions:
        raise ToolError(
            f"Open-position limit reached: "
            f"{len(open_positions)}/"
            f"{max_open_positions}.",
            EXIT_PERMISSION_DENIED,
        )

    state = load_state()

    daily_pnl = safe_float(
        state.get(
            "daily_realized_pnl",
            0,
        )
    )

    if daily_pnl <= -abs(
        max_daily_loss
    ):
        raise ToolError(
            "Daily loss circuit breaker active.",
            EXIT_PERMISSION_DENIED,
        )

    wallet = get_wallet(
        client
    )

    equity = wallet_equity_usdt(
        wallet
    )

    if equity <= 0:
        raise ToolError(
            "Unable to establish positive "
            "USDT account equity; refusing live entry.",
            EXIT_PERMISSION_DENIED,
        )

    risk_usdt = (
        notional
        * (
            stop_distance
            / entry_price
        )
    )

    max_risk_usdt = (
        equity
        * max_account_risk_pct
        / 100.0
    )

    if risk_usdt > max_risk_usdt:
        raise ToolError(
            f"Estimated stop risk "
            f"{risk_usdt:.8f} USDT exceeds "
            f"account-risk allowance "
            f"{max_risk_usdt:.8f} USDT.",
            EXIT_PERMISSION_DENIED,
        )

    return {
        "equity_usdt":
            equity,
        "estimated_risk_usdt":
            risk_usdt,
        "max_risk_usdt":
            max_risk_usdt,
        "open_positions":
            len(open_positions),
    }


# ==============================================================================
# EXECUTION
# ==============================================================================

def handle_execute_scalp(
    client: BybitV5,
    symbol: str,
    side: str,
    qty: Optional[float],
    target_profit: float,
    leverage: int,
    post_only: bool,
    live_request: bool,
    live_confirmation: str,
    max_spread: float,
    max_notional: float,
    max_account_risk_pct: float,
    max_daily_loss: float,
    max_open_positions: int,
    reduce_only: bool = False,
) -> Dict[str, Any]:
    sym = validate_symbol(
        symbol
    )

    if side not in {
        "Buy",
        "Sell",
    }:
        raise ToolError(
            "Side must be Buy or Sell.",
            EXIT_INVALID_INPUT,
        )

    if leverage < 1:
        raise ToolError(
            "Leverage must be >= 1.",
            EXIT_INVALID_INPUT,
        )

    max_leverage = int(
        safe_float(
            cfg_value(
                "MAX_LEVERAGE",
                "MAX_LEVERAGE",
                "25",
            ),
            25,
        )
    )

    if leverage > max_leverage:
        raise ToolError(
            f"Requested leverage {leverage}x "
            f"exceeds configured maximum "
            f"{max_leverage}x.",
            EXIT_PERMISSION_DENIED,
        )

    live, auth_reason = live_authorized(
        client.testnet,
        live_request,
        live_confirmation,
    )

    state = load_state()

    if (
        state.get("kill_switch")
        or kill_switch_active()
    ):
        raise ToolError(
            "Execution blocked: kill switch active.",
            EXIT_PERMISSION_DENIED,
        )

    cooldown = safe_float(
        cfg_value(
            "LLM_AGENT_VAR_COOLDOWN_SECONDS",
            "COOLDOWN_SECONDS",
            "60",
        ),
        60,
    )

    elapsed = (
        time.time()
        - safe_float(
            state.get(
                "last_execution_ts",
                0,
            )
        )
    )

    # Cooldown applies only to new entries.
    if (
        not reduce_only
        and elapsed < cooldown
    ):
        raise ToolError(
            f"Cooldown active: "
            f"{cooldown - elapsed:.1f}s remaining.",
            EXIT_ERROR,
        )

    # Re-scan immediately before execution.
    scan = handle_scan(
        client,
        sym,
        max_spread,
        target_profit,
        leverage,
        max_notional,
        safe_float(
            cfg_value(
                "LLM_AGENT_VAR_MAKER_FEE_PCT",
                "MAKER_FEE_PCT",
                "0.020",
            ),
            0.020,
        ),
        safe_float(
            cfg_value(
                "LLM_AGENT_VAR_TAKER_FEE_PCT",
                "TAKER_FEE_PCT",
                "0.055",
            ),
            0.055,
        ),
    )

    if not reduce_only and not scan["risk_gate_pass"]:
        raise ToolError(
            "Entry risk gates failed.",
            EXIT_PERMISSION_DENIED,
            {
                "scan": scan,
            },
        )

    tick, step, min_qty, max_qty = (
        fetch_precision(
            client,
            sym,
        )
    )

    plan = scan[
        "micro_profit_plan"
    ]

    requested_qty = (
        qty
        if qty is not None
        else plan[
            "calculated_qty"
        ]
    )

    final_qty, qty_string = (
        clamp_decimal_qty(
            requested_qty,
            step,
            min_qty,
            max_qty,
        )
    )

    # Re-read orderbook immediately before constructing price.
    orderbook = get_orderbook(
        client,
        sym,
    )

    if (
        orderbook["spread_pct"]
        > max_spread
        and not reduce_only
    ):
        raise ToolError(
            "Spread widened beyond configured "
            "execution threshold during re-check.",
            EXIT_PERMISSION_DENIED,
        )

    entry_price = (
        orderbook["best_bid"]
        if side == "Buy"
        else orderbook["best_ask"]
    )

    notional = (
        final_qty * entry_price
    )

    if notional > max_notional:
        raise ToolError(
            f"Order notional "
            f"{notional:.8f} exceeds "
            f"limit {max_notional:.8f}.",
            EXIT_PERMISSION_DENIED,
        )

    stop_distance = safe_float(
        plan[
            "stop_distance"
        ]
    )

    required_delta = safe_float(
        plan[
            "required_price_delta"
        ]
    )

    stop_price = (
        entry_price - stop_distance
        if side == "Buy"
        else entry_price + stop_distance
    )

    take_profit = (
        entry_price + required_delta
        if side == "Buy"
        else entry_price - required_delta
    )

    # Entry risk checks are intentionally skipped for reduce-only
    # because a close decreases exposure.
    risk = None

    if not reduce_only:
        risk = enforce_entry_risk(
            client,
            sym,
            notional,
            stop_distance,
            entry_price,
            max_notional,
            max_account_risk_pct,
            max_daily_loss,
            max_open_positions,
        )

    entry_price_fmt = quantize_step(
        entry_price,
        tick,
        ROUND_DOWN
        if side == "Buy"
        else ROUND_UP,
    )

    stop_fmt = quantize_step(
        stop_price,
        tick,
        ROUND_DOWN
        if side == "Buy"
        else ROUND_UP,
    )

    tp_fmt = quantize_step(
        take_profit,
        tick,
        ROUND_UP
        if side == "Buy"
        else ROUND_DOWN,
    )

    order_link_id = (
        "AI8"
        + hashlib.sha256(
            (
                f"{sym}:"
                f"{side}:"
                f"{time.time_ns()}:"
                f"{uuid.uuid4()}"
            ).encode()
        ).hexdigest()[:27]
    )

    record = {
        "success": True,
        "symbol": sym,
        "side": side,
        "qty": qty_string,
        "notional_usdt":
            round(
                notional,
                8,
            ),
        "leverage":
            f"{leverage}x",
        "entry_price":
            entry_price_fmt,
        "stop_loss":
            None
            if reduce_only
            else stop_fmt,
        "take_profit":
            None
            if reduce_only
            else tp_fmt,
        "reduce_only":
            reduce_only,
        "post_only":
            post_only,
        "mode":
            "LIVE"
            if live
            else "DRY_RUN",
        "authorization":
            auth_reason,
        "order_link_id":
            order_link_id,
        "timestamp":
            utc_now(),
    }

    if risk is not None:
        record["risk"] = risk

    # --------------------------------------------------------------------------
    # DRY RUN
    # --------------------------------------------------------------------------
    if not live:
        record["order_id"] = "SIMULATED"
        append_log(record)
        return record

    # --------------------------------------------------------------------------
    # LIVE LEVERAGE
    # --------------------------------------------------------------------------
    client.request(
        "POST",
        "/v5/position/set-leverage",
        data={
            "category":
                "linear",
            "symbol":
                sym,
            "buyLeverage":
                str(leverage),
            "sellLeverage":
                str(leverage),
        },
    )

    # --------------------------------------------------------------------------
    # Determine position index.
    # auto => use current position mode where possible.
    # --------------------------------------------------------------------------
    position_idx = 0

    try:
        positions = get_positions(
            client,
            sym,
        )

        configured_mode = cfg_value(
            "POSITION_MODE",
            "POSITION_MODE",
            "auto",
        ).lower()

        if configured_mode == "hedge":
            position_idx = (
                1
                if side == "Buy"
                else 2
            )
        elif configured_mode == "oneway":
            position_idx = 0
        elif positions:
            # Preserve an already-open position index if present.
            for p in positions:
                if p.get("positionIdx") is not None:
                    position_idx = int(
                        safe_float(
                            p.get(
                                "positionIdx"
                            ),
                            0,
                        )
                    )
                    break

    except Exception:
        position_idx = 0

    # --------------------------------------------------------------------------
    # Bybit reduceOnly orders cannot carry TP/SL.
    # --------------------------------------------------------------------------
    payload: Dict[str, Any] = {
        "category":
            "linear",
        "symbol":
            sym,
        "side":
            side,
        "orderType":
            "Limit"
            if post_only
            else "Market",
        "qty":
            qty_string,
        "positionIdx":
            position_idx,
        "orderLinkId":
            order_link_id,
        "reduceOnly":
            reduce_only,
    }

    if post_only:
        payload.update({
            "price":
                entry_price_fmt,
            "timeInForce":
                "PostOnly",
        })
    else:
        payload.update({
            "timeInForce":
                "IOC",
        })

    if reduce_only:
        payload["closeOnTrigger"] = True
    else:
        payload.update({
            "takeProfit":
                tp_fmt,
            "stopLoss":
                stop_fmt,
            "tpTriggerBy":
                cfg_value(
                    "TRIGGER_BY",
                    "TRIGGER_BY",
                    "MarkPrice",
                ),
            "slTriggerBy":
                cfg_value(
                    "TRIGGER_BY",
                    "TRIGGER_BY",
                    "MarkPrice",
                ),
            "tpslMode":
                "Full",
            "tpOrderType":
                "Market",
            "slOrderType":
                "Market",
        })

    result = client.request(
        "POST",
        "/v5/order/create",
        data=payload,
    )

    order_id = result.get(
        "orderId"
    )

    if not order_id:
        raise ToolError(
            "Bybit accepted the request but "
            "returned no orderId.",
            EXIT_ERROR,
        )

    record["order_id"] = order_id
    record["position_idx"] = position_idx
    record["acknowledged"] = True
    record["fill_confirmed"] = False

    state["last_execution_ts"] = time.time()
    state["execution_attempts"] = (
        int(
            state.get(
                "execution_attempts",
                0,
            )
        )
        + 1
    )
    state["last_order_id"] = order_id
    state["last_order_link_id"] = (
        order_link_id
    )

    save_state(state)
    append_log(record)

    return record


# ==============================================================================
# CLOSE POSITION
# ==============================================================================

def handle_close_position(
    client: BybitV5,
    symbol: str,
    side: str,
    qty: Optional[float],
    leverage: int,
    live: bool,
    confirmation: str,
    max_notional: float,
) -> Dict[str, Any]:
    sym = validate_symbol(
        symbol
    )

    positions = get_positions(
        client,
        sym,
    )

    active = [
        p
        for p in positions
        if position_is_open(p)
    ]

    if not active:
        raise ToolError(
            f"No open position found for {sym}."
        )

    selected = active[0]

    position_side = str(
        selected.get(
            "side",
            "",
        )
    )

    position_size = safe_float(
        selected.get(
            "size",
            0,
        )
    )

    if position_size <= 0:
        raise ToolError(
            "Open position has invalid size."
        )

    close_side = (
        "Sell"
        if position_side == "Buy"
        else "Buy"
    )

    if side in {"Buy", "Sell"}:
        # Explicit side is accepted only when it
        # actually closes the selected position.
        if side == close_side:
            close_side = side

    return handle_execute_scalp(
        client=client,
        symbol=sym,
        side=close_side,
        qty=qty or position_size,
        target_profit=0.01,
        leverage=leverage,
        post_only=False,
        live_request=live,
        live_confirmation=confirmation,
        max_spread=100.0,
        max_notional=max_notional,
        max_account_risk_pct=100.0,
        max_daily_loss=1e18,
        max_open_positions=999999,
        reduce_only=True,
    )


# ==============================================================================
# ACTION ROUTER
# ==============================================================================

def execute_tool(
    action: str,
    symbol: str = "SOLUSDT",
    timeframe: str = "1",
    side: str = "Buy",
    target_profit_usdt: float = 0.10,
    qty: Optional[float] = None,
    leverage: int = 10,
    max_spread_pct: float = 0.035,
    max_notional_usdt: float = 100.0,
    post_only: bool = True,
    live: bool = False,
    live_confirmation: str = "",
    reduce_only: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()

    if action not in VALID_ACTIONS:
        return {
            "success": False,
            "error":
                f"Invalid action '{action}'.",
            "valid_actions":
                sorted(VALID_ACTIONS),
            "exit_code":
                EXIT_INVALID_INPUT,
            "duration_ms":
                0.0,
        }

    symbol = validate_symbol(
        symbol
    )

    testnet = parse_bool(
        cfg_value(
            "LLM_AGENT_VAR_USE_TESTNET",
            "BYBIT_TESTNET",
            "true",
        ),
        True,
    )

    maker_fee = safe_float(
        cfg_value(
            "LLM_AGENT_VAR_MAKER_FEE_PCT",
            "MAKER_FEE_PCT",
            "0.020",
        ),
        0.020,
    )

    taker_fee = safe_float(
        cfg_value(
            "LLM_AGENT_VAR_TAKER_FEE_PCT",
            "TAKER_FEE_PCT",
            "0.055",
        ),
        0.055,
    )

    max_account_risk_pct = safe_float(
        cfg_value(
            "LLM_AGENT_VAR_MAX_ACCOUNT_RISK_PCT",
            "MAX_ACCOUNT_RISK_PCT",
            "0.50",
        ),
        0.50,
    )

    max_daily_loss = safe_float(
        cfg_value(
            "LLM_AGENT_VAR_MAX_DAILY_LOSS_USDT",
            "MAX_DAILY_LOSS_USDT",
            "5",
        ),
        5,
    )

    max_open_positions = int(
        safe_float(
            cfg_value(
                "LLM_AGENT_VAR_MAX_OPEN_POSITIONS",
                "MAX_OPEN_POSITIONS",
                "1",
            ),
            1,
        )
    )

    client = BybitV5(
        testnet=testnet
    )

    cache = ToolCache()

    cache_key = build_cache_key(
        action,
        symbol,
        timeframe,
        side,
        target_profit_usdt,
        qty,
        leverage,
        max_spread_pct,
        max_notional_usdt,
        post_only,
        live,
        reduce_only,
    )

    cacheable_actions = {
        "ta",
        "scan",
        "calc_micro_profit",
        "orderbook",
        "ticker",
        "instrument",
    }

    if (
        use_cache
        and not live
        and action in cacheable_actions
    ):
        cached = cache.get(
            cache_key,
            ttl_seconds=5,
        )

        if cached:
            cached["cached"] = True
            return cached

    try:
        with GracefulShutdown() as shutdown:
            if shutdown.should_stop():
                raise ToolError(
                    "Execution interrupted.",
                    EXIT_INTERRUPTED,
                )

            if action == "ta":
                result = handle_ta(
                    client,
                    symbol,
                    timeframe,
                )

            elif action == "scan":
                result = handle_scan(
                    client,
                    symbol,
                    max_spread_pct,
                    target_profit_usdt,
                    leverage,
                    max_notional_usdt,
                    maker_fee,
                    taker_fee,
                )

            elif action == "calc_micro_profit":
                scan = handle_scan(
                    client,
                    symbol,
                    max_spread_pct,
                    target_profit_usdt,
                    leverage,
                    max_notional_usdt,
                    maker_fee,
                    taker_fee,
                )

                result = {
                    "success": True,
                    "symbol": symbol,
                    "plan":
                        scan[
                            "micro_profit_plan"
                        ],
                    "risk_gate_pass":
                        scan[
                            "risk_gate_pass"
                        ],
                }

            elif action == "orderbook":
                result = {
                    "success": True,
                    "symbol": symbol,
                    "orderbook":
                        get_orderbook(
                            client,
                            symbol,
                        ),
                }

            elif action == "ticker":
                result = {
                    "success": True,
                    "symbol": symbol,
                    "ticker":
                        get_ticker(
                            client,
                            symbol,
                        ),
                }

            elif action == "instrument":
                result = {
                    "success": True,
                    "symbol": symbol,
                    "instrument":
                        fetch_instrument(
                            client,
                            symbol,
                        ),
                }

            elif action == "wallet_balance":
                result = {
                    "success": True,
                    "wallet":
                        get_wallet(
                            client
                        ),
                }

            elif action == "positions":
                result = {
                    "success": True,
                    "positions":
                        get_positions(
                            client,
                            symbol,
                        ),
                }

            elif action == "open_orders":
                result = {
                    "success": True,
                    "orders":
                        get_open_orders(
                            client,
                            symbol,
                        ),
                }

            elif action == "fills":
                result = {
                    "success": True,
                    "fills":
                        get_fills(
                            client,
                            symbol,
                        ),
                }

            elif action == "price_action":
                candles = get_closed_klines(
                    client,
                    symbol,
                    timeframe,
                    100,
                )

                result = {
                    "success": True,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "price_action":
                        price_action(
                            candles
                        ),
                }

            elif action == "risk_status":
                result = risk_status(
                    client,
                    symbol,
                    max_notional_usdt,
                    max_open_positions,
                    max_daily_loss,
                )

            elif action == "execute_scalp":
                result = handle_execute_scalp(
                    client=client,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    target_profit=target_profit_usdt,
                    leverage=leverage,
                    post_only=post_only,
                    live_request=live,
                    live_confirmation=live_confirmation,
                    max_spread=max_spread_pct,
                    max_notional=max_notional_usdt,
                    max_account_risk_pct=max_account_risk_pct,
                    max_daily_loss=max_daily_loss,
                    max_open_positions=max_open_positions,
                    reduce_only=reduce_only,
                )

            elif action == "close_position":
                result = handle_close_position(
                    client,
                    symbol,
                    side,
                    qty,
                    leverage,
                    live,
                    live_confirmation,
                    max_notional_usdt,
                )

            elif action == "cancel_orders":
                if not live:
                    result = {
                        "success": True,
                        "mode": "DRY_RUN",
                        "message":
                            "Cancel-all acknowledged "
                            "as dry run.",
                    }
                else:
                    authorized, reason = live_authorized(
                        client.testnet,
                        True,
                        live_confirmation,
                    )

                    if not authorized:
                        raise ToolError(
                            f"Live cancellation blocked: "
                            f"{reason}.",
                            EXIT_PERMISSION_DENIED,
                        )

                    cancelled = client.request(
                        "POST",
                        "/v5/order/cancel-all",
                        data={
                            "category":
                                "linear",
                            "symbol":
                                symbol,
                        },
                    )

                    result = {
                        "success": True,
                        "mode": "LIVE",
                        "result":
                            cancelled,
                    }

            elif action == "set_leverage":
                if not live:
                    result = {
                        "success": True,
                        "mode": "DRY_RUN",
                        "message":
                            f"Set leverage to "
                            f"{leverage}x acknowledged.",
                    }
                else:
                    authorized, reason = live_authorized(
                        client.testnet,
                        True,
                        live_confirmation,
                    )

                    if not authorized:
                        raise ToolError(
                            f"Live leverage change blocked: "
                            f"{reason}.",
                            EXIT_PERMISSION_DENIED,
                        )

                    leverage_result = client.request(
                        "POST",
                        "/v5/position/set-leverage",
                        data={
                            "category":
                                "linear",
                            "symbol":
                                symbol,
                            "buyLeverage":
                                str(leverage),
                            "sellLeverage":
                                str(leverage),
                        },
                    )

                    result = {
                        "success": True,
                        "mode": "LIVE",
                        "result":
                            leverage_result,
                    }

            elif action == "kill_switch":
                result = set_kill_switch(
                    bool(
                        reduce_only
                        if reduce_only is not None
                        else True
                    )
                )

            elif action == "history":
                path = trade_log_file()

                lines = (
                    path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if path.exists()
                    else []
                )

                recent = []

                for line in lines[-50:]:
                    if not line.strip():
                        continue

                    try:
                        recent.append(
                            json.loads(line)
                        )
                    except Exception:
                        continue

                result = {
                    "success": True,
                    "recent_trades":
                        recent,
                }

            else:
                raise ToolError(
                    f"Unhandled action '{action}'.",
                    EXIT_INVALID_INPUT,
                )

        result["action"] = action
        result["duration_ms"] = round(
            (
                time.monotonic()
                - started
            )
            * 1000,
            2,
        )
        result["exit_code"] = (
            EXIT_SUCCESS
        )

        if (
            use_cache
            and not live
            and action in cacheable_actions
        ):
            cache.set(
                cache_key,
                result,
            )

        return result

    except ToolError as exc:
        result = exc.to_dict()
        result.update({
            "action": action,
            "duration_ms": round(
                (
                    time.monotonic()
                    - started
                )
                * 1000,
                2,
            ),
        })

        return result

    except Exception as exc:
        if verbose:
            logging.exception(
                "Unhandled tool exception"
            )

        return {
            "success": False,
            "action": action,
            "error": str(exc),
            "exit_code":
                EXIT_ERROR,
            "duration_ms": round(
                (
                    time.monotonic()
                    - started
                )
                * 1000,
                2,
            ),
        }


# ==============================================================================
# SELF TEST
# ==============================================================================

def run_self_test() -> Dict[str, Any]:
    checks = []

    def check(
        name: str,
        condition: bool,
        detail: str = "",
    ):
        checks.append({
            "name": name,
            "pass": bool(condition),
            "detail": detail,
        })

    check(
        "python_version",
        sys.version_info >= (3, 9),
        platform.python_version(),
    )

    check(
        "data_directory",
        get_data_dir().is_dir(),
        str(get_data_dir()),
    )

    check(
        "state_write",
        True,
        "atomic state writer available",
    )

    check(
        "json_encoder",
        json.dumps(
            {
                "decimal":
                    Decimal("1.25")
            },
            cls=ToolJSONEncoder,
        ),
        "JSON encoder available",
    )

    check(
        "symbol_validation",
        validate_symbol(
            "SOLUSDT"
        ) == "SOLUSDT",
    )

    check(
        "timeframe_validation",
        validate_timeframe(
            "5"
        ) == "5",
    )

    check(
        "decimal_quantization",
        quantize_step(
            "1.23456",
            "0.01",
        ) == "1.23",
    )

    candles = normalize_candles(
        [
            [
                "3000",
                "3",
                "4",
                "2",
                "3.5",
                "10",
                "30",
            ],
            [
                "2000",
                "2",
                "3",
                "1",
                "2.5",
                "10",
                "25",
            ],
            [
                "4000",
                "4",
                "5",
                "3",
                "4.5",
                "10",
                "45",
            ],
        ],
        True,
    )

    check(
        "candle_normalization",
        len(candles) == 2
        and candles[0][0] == "2000",
        "latest candle removed",
    )

    check(
        "live_default_block",
        not live_authorized(
            True,
            True,
            "I_UNDERSTAND_LIVE_RISK",
        )[0],
        "testnet cannot authorize live orders",
    )

    check(
        "kill_switch_default",
        isinstance(
            kill_switch_active(),
            bool,
        ),
    )

    passed = all(
        x["pass"]
        for x in checks
    )

    return {
        "success": passed,
        "version": __version__,
        "checks": checks,
        "timestamp": utc_now(),
    }


# ==============================================================================
# OUTPUT
# ==============================================================================

def write_llm_output(
    data: dict[str, Any],
) -> None:
    path = os.environ.get(
        "LLM_OUTPUT",
        "/dev/stdout",
    )

    payload = json.dumps(
        data,
        ensure_ascii=False,
        cls=ToolJSONEncoder,
    )

    if path in {
        "/dev/stdout",
        "/dev/fd/1",
        "-",
    }:
        sys.stdout.write(
            payload + "\n"
        )
        sys.stdout.flush()
        return

    try:
        target = Path(path)
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with target.open(
            "a",
            encoding="utf-8",
        ) as fp:
            fp.write(
                payload + "\n"
            )

    except OSError:
        sys.stdout.write(
            payload + "\n"
        )
        sys.stdout.flush()


def generate_tool_schema() -> Dict[str, Any]:
    return {
        "name":
            "bybit_trader",
        "description":
            "Hardened Bybit V5 USDT perpetual "
            "market analytics, risk management, "
            "and guarded execution tool.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum":
                        sorted(
                            VALID_ACTIONS
                        ),
                },
                "symbol": {
                    "type": "string",
                },
                "timeframe": {
                    "type": "string",
                    "enum":
                        sorted(
                            VALID_TIMEFRAMES
                        ),
                },
                "side": {
                    "type": "string",
                    "enum": [
                        "Buy",
                        "Sell",
                    ],
                },
                "target_profit_usdt": {
                    "type": "number",
                },
                "qty": {
                    "type": "number",
                },
                "leverage": {
                    "type": "integer",
                },
                "post_only": {
                    "type": "boolean",
                },
                "live": {
                    "type": "boolean",
                },
                "live_confirmation": {
                    "type": "string",
                },
                "reduce_only": {
                    "type": "boolean",
                },
                "max_spread_pct": {
                    "type": "number",
                },
                "max_notional_usdt": {
                    "type": "number",
                },
                "use_cache": {
                    "type": "boolean",
                },
            },
            "required": [
                "action"
            ],
        },
    }


# ==============================================================================
# RUN / CLI
# ==============================================================================

def run(
    action: str,
    symbol: str = "SOLUSDT",
    timeframe: str = "1",
    side: str = "Buy",
    target_profit_usdt: float = 0.10,
    qty: Optional[float] = None,
    leverage: int = 10,
    max_spread_pct: float = 0.035,
    max_notional_usdt: float = 100.0,
    post_only: bool = True,
    live: bool = False,
    live_confirmation: str = "",
    reduce_only: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    result = execute_tool(
        action=action,
        symbol=symbol,
        timeframe=timeframe,
        side=side,
        target_profit_usdt=target_profit_usdt,
        qty=qty,
        leverage=leverage,
        max_spread_pct=max_spread_pct,
        max_notional_usdt=max_notional_usdt,
        post_only=post_only,
        live=live,
        live_confirmation=live_confirmation,
        reduce_only=reduce_only,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(
        result,
        no_color=no_color,
    )

    write_llm_output(
        result
    )


def main() -> int:
    args = sys.argv[1:]

    # --------------------------------------------------------------------------
    # AIChat instruction hook
    # --------------------------------------------------------------------------
    if args and args[0] == "_instructions":
        cfg = get_env_config()

        testnet = parse_bool(
            cfg_value(
                "LLM_AGENT_VAR_USE_TESTNET",
                "BYBIT_TESTNET",
                "true",
            ),
            True,
        )

        enabled = parse_bool(
            cfg_value(
                "LLM_AGENT_VAR_LIVE_TRADING_ENABLED",
                "LIVE_TRADING_ENABLED",
                "false",
            ),
            False,
        )

        allowed = parse_bool(
            cfg_value(
                "ALLOW_LIVE_TRADING",
                "ALLOW_LIVE_TRADING",
                "false",
            ),
            False,
        )

        prompt = f"""You are operating as bybit_trader.

Status:
- Version: {__version__}
- Network: {'TESTNET' if testnet else 'MAINNET'}
- Live Trading Enabled: {'YES' if enabled else 'NO'}
- Secondary Live Authorization: {'YES' if allowed else 'NO'}
- Effective Live Trading: {'NO' if testnet or not enabled or not allowed else 'REQUIRES CONFIRMATION'}
- Kill Switch: {'ACTIVE' if kill_switch_active() else 'OFF'}
- Agent CWD: {get_builtin_var('__cwd__') or os.getcwd()}
- UTC Time: {utc_now()}

Execution policy:
- Default is dry-run.
- Opening trades require scan + risk gates.
- Live trading requires explicit confirmation.
- Reduce-only closes bypass directional entry signals.
- Never claim an order is filled merely because it was acknowledged.
"""

        write_llm_output({
            "instructions": prompt
        })

        return EXIT_SUCCESS

    if args and args[0] == "bybit_trader":
        args = args[1:]

    # --------------------------------------------------------------------------
    # JSON input mode
    # --------------------------------------------------------------------------
    if (
        args
        and not args[0].startswith("-")
    ) or (
        not sys.stdin.isatty()
        and not args
    ):
        raw = (
            args[0]
            if args
            else sys.stdin.read()
        )

        try:
            params = (
                json.loads(raw)
                if raw.strip()
                else {}
            )
        except Exception as exc:
            result = {
                "success": False,
                "error":
                    f"Invalid JSON payload: {exc}",
                "exit_code":
                    EXIT_INVALID_INPUT,
            }

            write_llm_output(
                result
            )

            return EXIT_INVALID_INPUT

        result = execute_tool(
            action=params.get(
                "action",
                "ta",
            ),
            symbol=params.get(
                "symbol"
            )
            or cfg_value(
                "LLM_AGENT_VAR_DEFAULT_SYMBOL",
                "DEFAULT_SYMBOL",
                "SOLUSDT",
            ),
            timeframe=params.get(
                "timeframe",
                "1",
            ),
            side=params.get(
                "side",
                "Buy",
            ),
            target_profit_usdt=safe_float(
                params.get(
                    "target_profit_usdt"
                )
                or cfg_value(
                    "LLM_AGENT_VAR_TARGET_MICRO_PROFIT",
                    "TARGET_MICRO_PROFIT",
                    "0.10",
                ),
                0.10,
            ),
            qty=(
                safe_float(
                    params["qty"]
                )
                if (
                    "qty" in params
                    and params["qty"]
                    is not None
                )
                else None
            ),
            leverage=int(
                safe_float(
                    params.get(
                        "leverage"
                    )
                    or cfg_value(
                        "LLM_AGENT_VAR_DEFAULT_LEVERAGE",
                        "DEFAULT_LEVERAGE",
                        "10",
                    ),
                    10,
                )
            ),
            max_spread_pct=safe_float(
                params.get(
                    "max_spread_pct"
                )
                or cfg_value(
                    "LLM_AGENT_VAR_MAX_SPREAD_PCT",
                    "MAX_SPREAD_PCT",
                    "0.035",
                ),
                0.035,
            ),
            max_notional_usdt=safe_float(
                params.get(
                    "max_notional_usdt"
                )
                or cfg_value(
                    "LLM_AGENT_VAR_MAX_NOTIONAL_USDT",
                    "MAX_NOTIONAL_USDT",
                    "100",
                ),
                100,
            ),
            post_only=parse_bool(
                params.get(
                    "post_only",
                    True,
                ),
                True,
            ),
            live=parse_bool(
                params.get(
                    "live",
                    False,
                ),
                False,
            ),
            live_confirmation=str(
                params.get(
                    "live_confirmation",
                    "",
                )
            ),
            reduce_only=parse_bool(
                params.get(
                    "reduce_only",
                    False,
                ),
                False,
            ),
            use_cache=parse_bool(
                params.get(
                    "use_cache",
                    False,
                ),
                False,
            ),
        )

        print_human_readable_ui(
            result
        )

        write_llm_output(
            result
        )

        return int(
            result.get(
                "exit_code",
                EXIT_SUCCESS,
            )
        )

    # --------------------------------------------------------------------------
    # CLI
    # --------------------------------------------------------------------------
    parser = argparse.ArgumentParser(
        prog="bybit_trader",
        description=(
            f"AIChat Bybit V5 Perpetual "
            f"Tool v{__version__}"
        ),
    )

    parser.add_argument(
        "--action",
        "-a",
        choices=sorted(
            VALID_ACTIONS
        ),
        default="ta",
    )

    parser.add_argument(
        "--symbol",
        "-s",
        default=cfg_value(
            "LLM_AGENT_VAR_DEFAULT_SYMBOL",
            "DEFAULT_SYMBOL",
            "SOLUSDT",
        ),
    )

    parser.add_argument(
        "--timeframe",
        "-t",
        choices=sorted(
            VALID_TIMEFRAMES
        ),
        default="1",
    )

    parser.add_argument(
        "--side",
        choices=[
            "Buy",
            "Sell",
        ],
        default="Buy",
    )

    parser.add_argument(
        "--target-profit-usdt",
        type=float,
        default=safe_float(
            cfg_value(
                "LLM_AGENT_VAR_TARGET_MICRO_PROFIT",
                "TARGET_MICRO_PROFIT",
                "0.10",
            ),
            0.10,
        ),
    )

    parser.add_argument(
        "--qty",
        type=float,
        default=None,
    )

    parser.add_argument(
        "--leverage",
        type=int,
        default=int(
            safe_float(
                cfg_value(
                    "LLM_AGENT_VAR_DEFAULT_LEVERAGE",
                    "DEFAULT_LEVERAGE",
                    "10",
                ),
                10,
            )
        ),
    )

    parser.add_argument(
        "--max-spread-pct",
        type=float,
        default=safe_float(
            cfg_value(
                "LLM_AGENT_VAR_MAX_SPREAD_PCT",
                "MAX_SPREAD_PCT",
                "0.035",
            ),
            0.035,
        ),
    )

    parser.add_argument(
        "--max-notional-usdt",
        type=float,
        default=safe_float(
            cfg_value(
                "LLM_AGENT_VAR_MAX_NOTIONAL_USDT",
                "MAX_NOTIONAL_USDT",
                "100",
            ),
            100,
        ),
    )

    parser.add_argument(
        "--post-only",
        action="store_true",
        default=True,
    )

    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--live-confirmation",
        default="",
        help="Required exact live-trading confirmation token.",
    )

    parser.add_argument(
        "--reduce-only",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--clear-cache",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--schema",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
    )

    parsed = parser.parse_args(
        args
    )

    if parsed.schema:
        sys.stdout.write(
            json.dumps(
                generate_tool_schema(),
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )

        return EXIT_SUCCESS

    if parsed.self_test:
        result = run_self_test()

        print_human_readable_ui(
            result,
            no_color=parsed.no_color,
        )

        write_llm_output(
            result
        )

        return (
            EXIT_SUCCESS
            if result["success"]
            else EXIT_ERROR
        )

    if parsed.clear_cache:
        count = invalidate_cache()

        _cprint(
            f"{NEON_GREEN}"
            f"Cleared {count} cache entry/entries."
            f"{RESET}",
            no_color=parsed.no_color,
        )

        return EXIT_SUCCESS

    result = execute_tool(
        action=parsed.action,
        symbol=parsed.symbol,
        timeframe=parsed.timeframe,
        side=parsed.side,
        target_profit_usdt=parsed.target_profit_usdt,
        qty=parsed.qty,
        leverage=parsed.leverage,
        max_spread_pct=parsed.max_spread_pct,
        max_notional_usdt=parsed.max_notional_usdt,
        post_only=parsed.post_only,
        live=parsed.live,
        live_confirmation=parsed.live_confirmation,
        reduce_only=parsed.reduce_only,
        use_cache=parsed.use_cache,
        no_color=parsed.no_color,
        verbose=parsed.verbose,
    )

    print_human_readable_ui(
        result,
        no_color=parsed.no_color,
    )

    write_llm_output(
        result
    )

    return int(
        result.get(
            "exit_code",
            EXIT_SUCCESS,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
