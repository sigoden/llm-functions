import hashlib
import hmac
import json
import logging
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)  # Never override shell-exported vars

logger = logging.getLogger("BybitRealm")


@dataclass
class TradingConfig:
    api_key: str = field(default_factory=lambda: os.getenv("BYBIT_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BYBIT_API_SECRET", ""))
    testnet: bool = field(
        default_factory=lambda: (
            os.getenv("BYBIT_USE_TESTNET", "false").lower() == "true"
        )
    )

    # ── Network Settings ──────────────────────────────────────────────────
    use_proxy: bool = field(
        default_factory=lambda: os.getenv("PROXY_ENABLED", "false").lower() == "true"
    )
    proxy_host: str = field(
        default_factory=lambda: os.getenv("PROXY_HOST", "127.0.0.1")
    )
    proxy_port: int = field(
        default_factory=lambda: int(os.getenv("PROXY_PORT", "9050"))
    )
    proxy_type: str = field(default_factory=lambda: os.getenv("PROXY_TYPE", "socks5h"))
    use_pysocks: bool = field(
        default_factory=lambda: os.getenv("TOR_USE_PYSOCKS", "true").lower() == "true"
    )

    timeout: int = field(
        default_factory=lambda: int(os.getenv("REQUEST_TIMEOUT", "15"))
    )
    max_retries: int = field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "3")))
    recv_window: int = 5000
    journal_path: str = field(
        default_factory=lambda: os.getenv("JOURNAL_PATH", "bybit_journal.json")
    )
    db_path: str = field(
        default_factory=lambda: os.getenv("DB_PATH", "bybit_trades.db")
    )

    @property
    def base_url(self) -> str:
        return (
            "https://api-testnet.bybit.com" if self.testnet else "https://api.bybit.com"
        )

    @property
    def proxy_url(self) -> str:
        return f"{self.proxy_type}://{self.proxy_host}:{self.proxy_port}"


class RateLimiter:
    def __init__(self):
        self.buckets = {
            "trade": {
                "capacity": 10,
                "tokens": 10.0,
                "refill": 0.01,
                "last": time.time() * 1000,
            },
            "market": {
                "capacity": 50,
                "tokens": 50.0,
                "refill": 0.05,
                "last": time.time() * 1000,
            },
            "account": {
                "capacity": 20,
                "tokens": 20.0,
                "refill": 0.02,
                "last": time.time() * 1000,
            },
            "default": {
                "capacity": 20,
                "tokens": 20.0,
                "refill": 0.02,
                "last": time.time() * 1000,
            },
        }
        self.lock = threading.Lock()

    def acquire(self, category: str = "default"):
        with self.lock:
            bucket = self.buckets.get(category, self.buckets["default"])
            now = time.time() * 1000
            bucket["tokens"] = min(
                bucket["capacity"],
                max(0.0, bucket["tokens"] + (now - bucket["last"]) * bucket["refill"]),
            )
            bucket["last"] = now
            if bucket["tokens"] < 1:
                time.sleep((1 - bucket["tokens"]) / bucket["refill"] / 1000)
                bucket["tokens"] = 0
                bucket["last"] = time.time() * 1000
            bucket["tokens"] -= 1


class TradeJournal:
    def __init__(self, config: TradingConfig):
        self.db_path = Path(config.db_path)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, timestamp TEXT, action TEXT, symbol TEXT, payload TEXT, result TEXT, status TEXT)"
        )
        conn.commit()
        conn.close()

    def record(self, action: str, payload: dict, result: dict, symbol: str = "N/A"):
        trade_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        status = "success" if result.get("status") != "error" else "failed"
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    trade_id,
                    ts,
                    action,
                    symbol,
                    json.dumps(payload),
                    json.dumps(result),
                    status,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Journaling failed: {e}")
        return trade_id


class BybitBaseClient:
    def __init__(self, config: TradingConfig = None):
        self.config = config or TradingConfig()
        self.session = requests.Session()
        self._socks_session = None
        self._torsocks_bin = shutil.which("torsocks")

        if self.config.use_proxy:
            # Tier 1 Setup: PySocks (if requested)
            if self.config.use_pysocks:
                try:
                    import socks

                    self._socks_session = requests.Session()
                    logger.info(
                        f"Multi-tier Network Ready: Port {self.config.proxy_port}"
                    )
                except ImportError:
                    logger.warning(
                        "PySocks not available, falling back to requests proxy"
                    )

            # Standard session proxy setup
            proxy = self.config.proxy_url
            self.session.proxies = {"http": proxy, "https": proxy}

        self._limiter = RateLimiter()
        self._symbol_cache: Dict[str, dict] = {}
        self.time_offset = 0
        self.last_rate_limits = {}
        if os.getenv("BYBIT_SYNC_TIME", "true").lower() == "true":
            try:
                self.sync_server_time()
            except Exception:
                pass

    def sync_server_time(self):
        resp = self._request("GET", "/v5/market/time", signed=False)
        if isinstance(resp, dict) and "timeSecond" in resp:
            st = int(resp.get("timeSecond", time.time())) * 1000
            self.time_offset = st - int(time.time() * 1000)
            logger.debug(f"Server time synced. Offset: {self.time_offset}ms")

    def _sign(self, payload: str, ts: str) -> str:
        msg = f"{ts}{self.config.api_key}{self.config.recv_window}{payload}"
        return hmac.new(
            self.config.api_secret.encode(), msg.encode(), hashlib.sha256
        ).hexdigest()


    def _request(
        self,
        method,
        endpoint,
        params=None,
        json_data=None,
        signed=True,
        category="default",
    ) -> dict:
        if signed and (not self.config.api_key or not self.config.api_secret):
            return {
                "status": "error",
                "code": "NO_CREDENTIALS",
                "msg": "BYBIT_API_KEY / BYBIT_API_SECRET not set — set them in .env for signed endpoints.",
            }
        self._limiter.acquire(category)

        ts = str(int(time.time() * 1000) + self.time_offset)
        url = self.config.base_url + endpoint
        headers = {
            "X-BAPI-API-KEY": self.config.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": str(self.config.recv_window),
            "Content-Type": "application/json",
        }

        payload = ""
        if signed:
            if method == "GET":

                def format_val(v):
                    if isinstance(v, bool):
                        return str(v).lower()
                    return str(v)

                payload = "&".join(
                    f"{k}={format_val(v)}"
                    for k, v in sorted((params or {}).items())
                    if v is not None
                )
                if payload:
                    url += f"?{payload}"
                params = None
            elif json_data:
                clean_data = {}
                for k, v in json_data.items():
                    if isinstance(v, bool):
                        clean_data[k] = str(v).lower()
                    else:
                        clean_data[k] = v
                json_data = clean_data
                payload = json.dumps(json_data, sort_keys=True, separators=(",", ":"))
            headers["X-BAPI-SIGN"] = self._sign(payload, ts)

        # Multi-tier dispatching
        if not self.config.use_proxy:
            tiers = [self._tier_direct]
        elif signed:
            tiers = [self._tier_proxy, self._tier_pysocks, self._tier_direct]
        else:
            tiers = [
                self._tier_pysocks,
                self._tier_proxy,
                self._tier_torsocks,
                self._tier_direct,
            ]

        last_err = None
        for tier in tiers:
            for attempt in range(1, self.config.max_retries + 1):
                try:
                    resp_data = tier(method, url, headers, params, json_data)
                    if (
                        isinstance(resp_data, dict)
                        and resp_data.get("status") == "error"
                    ):
                        if resp_data.get("code") == 10006:  # Rate limit
                            time.sleep(attempt * 2.0)
                            continue
                        return resp_data
                    return resp_data
                except Exception as e:
                    last_err = str(e)
                    logger.debug(f"Tier {tier.__name__} attempt {attempt} failed: {e}")
                    time.sleep(0.5 * (2 ** (attempt - 1)))
            logger.warning(f"Tier {tier.__name__} exhausted")

        return {
            "status": "error",
            "msg": f"All network tiers exhausted. Last error: {last_err}",
        }

    def _tier_pysocks(self, method, url, headers, params, json_data):
        resp = self.session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            proxies={"http": self.config.proxy_url, "https": self.config.proxy_url},
            timeout=self.config.timeout,
        )
        return self._parse_response(resp)

    def _tier_proxy(self, method, url, headers, params, json_data):
        resp = self.session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=self.config.timeout,
        )
        return self._parse_response(resp)

    def _tier_torsocks(self, method, url, headers, params, json_data):
        if not self._torsocks_bin:
            raise RuntimeError("torsocks not found")
        cmd = [self._torsocks_bin, "curl", "-s", "-X", method]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        if json_data:
            cmd += ["-d", json.dumps(json_data, separators=(",", ":"))]
        full_url = url
        if params:

            def format_val(v):
                if isinstance(v, bool):
                    return str(v).lower()
                return str(v)

            qs = "&".join(f"{k}={format_val(v)}" for k, v in sorted(params.items()))
            full_url = f"{url}?{qs}"
        cmd.append(full_url)

        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=self.config.timeout + 5
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr)
        data = json.loads(proc.stdout)
        return (
            data.get("result", data)
            if data.get("retCode") == 0
            else {
                "status": "error",
                "code": data.get("retCode"),
                "msg": data.get("retMsg"),
            }
        )

    def _tier_direct(self, method, url, headers, params, json_data):
        resp = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=self.config.timeout,
        )
        return self._parse_response(resp)

    def _parse_response(self, resp):
        if "X-Bapi-Limit" in resp.headers:
            self.last_rate_limits["default"] = {
                "limit": resp.headers.get("X-Bapi-Limit"),
                "remaining": resp.headers.get("X-Bapi-Limit-Status"),
                "reset": resp.headers.get("X-Bapi-Limit-Reset-Timestamp"),
            }
        if resp.status_code == 200:
            data = resp.json()
            if data.get("retCode") == 0:
                return data.get("result", data)
            return {
                "status": "error",
                "code": data.get("retCode"),
                "msg": data.get("retMsg"),
            }
        return {
            "status": "error",
            "code": resp.status_code,
            "msg": f"HTTP {resp.status_code}",
        }

    def _format_qty(self, symbol, qty, category="linear"):
        try:
            info = self.get_instruments_info(category, symbol).get("list", [{}])[0]
            step = float(info.get("lotSizeFilter", {}).get("qtyStep", 0.001))
            prec = len(str(step).split(".")[-1]) if "." in str(step) else 0
            return f"{math.floor(qty / step) * step:.{prec}f}".rstrip("0").rstrip(".")
        except:
            return str(qty)

    def _format_price(self, symbol, price, category="linear"):
        try:
            info = self.get_instruments_info(category, symbol).get("list", [{}])[0]
            tick = float(info.get("priceFilter", {}).get("tickSize", 0.01))
            prec = len(str(tick).split(".")[-1]) if "." in str(tick) else 0
            return f"{round(price / tick) * tick:.{prec}f}"
        except:
            return str(price)

    def get_instruments_info(
        self, category: str = "linear", symbol: Optional[str] = None
    ) -> dict:
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET",
            "/v5/market/instruments-info",
            params,
            signed=False,
            category="market",
        )
