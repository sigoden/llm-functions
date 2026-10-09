# ─────────────────────────────────────────────────────────────────────
# config.py  –  lightweight, environment‑first configuration loader
# ─────────────────────────────────────────────────────────────────────
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# Default configuration
DEFAULT_CONFIG: Dict[str, Any] = {
    "symbol": "BTCUSDT",
    "interval": "15",
    "delay": 20,
    "use_tor": True,
    "once": False,
    "json_out": False,
    "sandbox": False,
    "log_rotate": True,
    "log_max_bytes": 5 * 1024 * 1024,  # 5 MiB
    "log_backup_count": 3,
}

_USER_CONFIG_FILE: Path = Path.home() / ".bybit_wbta.json"


def _load_user_config() -> Optional[Dict[str, Any]]:
    try:
        with _USER_CONFIG_FILE.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return None
    except Exception as exc:
        print(f"⛔  [CONFIG] Failed to load {_USER_CONFIG_FILE}: {exc}\n")
        return None


def _make_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = dict(DEFAULT_CONFIG)
    for key in list(DEFAULT_CONFIG):
        env_key = f"BYBIT_{key.upper()}"
        if env_key in os.environ:
            val = os.environ[env_key]
            if val.lower() in {"true", "1", "yes", "y"}:
                cfg[key] = True
            elif val.lower() in {"false", "0", "no", "n"}:
                cfg[key] = False
            elif val.isdigit():
                cfg[key] = int(val)
            else:
                cfg[key] = val
    user_cfg = _load_user_config()
    if user_cfg:
        cfg.update(user_cfg)
    return cfg


CONFIG = _make_config()
