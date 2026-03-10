from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
USER_CONFIG_PATH = ROOT_DIR / "user_config.txt"

DEFAULT_CONFIG: dict[str, Any] = {
    "tickers": [],
    "api_keys": {},
    "benchmark": "SPY",
    "horizon_days": 90,
    "history_years": 10,
    "as_of_date": "",
    "risk_budget_bps": 150,
}


def _parse_key_value_text(raw: str) -> dict[str, Any]:
    parsed: dict[str, Any] = dict(DEFAULT_CONFIG)
    parsed["tickers"] = []
    parsed["api_keys"] = {}
    collecting_tickers = False

    for line in raw.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue

        if cleaned.lower() == "tickers:":
            collecting_tickers = True
            continue

        if collecting_tickers and "=" not in cleaned:
            parsed["tickers"].append(cleaned.upper())
            continue

        if "=" not in cleaned:
            continue
        collecting_tickers = False

        key, value = cleaned.split("=", 1)
        key = key.strip().lower()
        value = value.strip()

        if key == "tickers":
            parsed["tickers"] = [t.strip().upper() for t in value.split(",") if t.strip()]
        elif key == "benchmark":
            parsed["benchmark"] = value.upper() or "SPY"
        elif key == "horizon_days":
            try:
                parsed["horizon_days"] = int(value)
            except ValueError:
                pass
        elif key == "history_years":
            try:
                parsed["history_years"] = int(value)
            except ValueError:
                pass
        elif key == "as_of_date":
            parsed["as_of_date"] = value
        elif key == "risk_budget_bps":
            try:
                parsed["risk_budget_bps"] = int(value)
            except ValueError:
                pass
        elif key.endswith("_api_key"):
            provider = key.replace("_api_key", "")
            if value:
                parsed["api_keys"][provider] = value

    return parsed


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def load_user_config() -> dict[str, Any]:
    if not USER_CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    parsed = _parse_key_value_text(USER_CONFIG_PATH.read_text(encoding="utf-8"))
    parsed["tickers"] = _dedupe_preserve_order(parsed.get("tickers", []))
    return parsed
