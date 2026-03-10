from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("finsense.macro")

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore[assignment]


def _fetch_fred_series(series_id: str, api_key: str, limit: int = 5) -> list[dict]:
    if not api_key or not _requests:
        return []
    try:
        resp = _requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("observations", [])
    except Exception as exc:
        logger.warning("FRED fetch error for %s: %s", series_id, exc)
        return []


def _latest_value(obs: list[dict]) -> float | None:
    for o in obs:
        val = o.get("value", ".")
        if val != ".":
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return None


def fetch_macro_context(api_key: str) -> dict[str, Any]:
    if not api_key:
        return _default_macro()
    fed_funds = _fetch_fred_series("FEDFUNDS", api_key)
    t10y2y = _fetch_fred_series("T10Y2Y", api_key)
    vix = _fetch_fred_series("VIXCLS", api_key)
    cpi = _fetch_fred_series("CPIAUCSL", api_key, limit=13)
    unemployment = _fetch_fred_series("UNRATE", api_key)
    credit_spread = _fetch_fred_series("BAMLH0A0HYM2", api_key)

    ff = _latest_value(fed_funds)
    spread_10y2y = _latest_value(t10y2y)
    vix_val = _latest_value(vix)
    unemp = _latest_value(unemployment)
    hy_spread = _latest_value(credit_spread)

    cpi_vals = [float(o["value"]) for o in cpi if o.get("value", ".") != "."]
    cpi_yoy = ((cpi_vals[0] / cpi_vals[-1]) - 1.0) * 100.0 if len(cpi_vals) >= 12 else None

    regime = classify_regime(
        fed_funds_rate=ff,
        yield_spread_10y2y=spread_10y2y,
        vix=vix_val,
        cpi_yoy=cpi_yoy,
        unemployment=unemp,
        hy_spread=hy_spread,
    )

    return {
        "regime": regime,
        "fed_funds_rate": ff,
        "yield_spread_10y2y": spread_10y2y,
        "vix": vix_val,
        "cpi_yoy_pct": round(cpi_yoy, 2) if cpi_yoy is not None else None,
        "unemployment_pct": unemp,
        "hy_credit_spread": hy_spread,
        "source": "FRED",
    }


def classify_regime(
    fed_funds_rate: float | None = None,
    yield_spread_10y2y: float | None = None,
    vix: float | None = None,
    cpi_yoy: float | None = None,
    unemployment: float | None = None,
    hy_spread: float | None = None,
) -> str:
    score = 0.0
    if vix is not None:
        if vix > 30:
            score -= 2.0
        elif vix > 20:
            score -= 0.5
        else:
            score += 1.0
    if yield_spread_10y2y is not None:
        if yield_spread_10y2y < 0:
            score -= 1.5
        elif yield_spread_10y2y < 0.5:
            score -= 0.5
        else:
            score += 0.5
    if cpi_yoy is not None:
        if cpi_yoy > 5.0:
            score -= 1.5
        elif cpi_yoy > 3.0:
            score -= 0.5
        elif cpi_yoy < 2.0:
            score += 0.5
    if hy_spread is not None:
        if hy_spread > 5.0:
            score -= 1.5
        elif hy_spread > 3.5:
            score -= 0.5
        else:
            score += 0.3
    if unemployment is not None:
        if unemployment > 6.0:
            score -= 1.0
        elif unemployment < 4.0:
            score += 0.5

    if score >= 1.5:
        return "growth"
    if score >= 0.0:
        return "disinflation"
    if score >= -1.5:
        return "inflation"
    return "slowdown"


def classify_regime_from_bench(bench_20d: float, bench_vol: float) -> str:
    if bench_20d > 0.03 and bench_vol < 0.018:
        return "growth"
    if bench_20d < -0.03 and bench_vol > 0.022:
        return "slowdown"
    if bench_vol > 0.025:
        return "inflation"
    return "disinflation"


def _default_macro() -> dict[str, Any]:
    return {
        "regime": "disinflation",
        "fed_funds_rate": None,
        "yield_spread_10y2y": None,
        "vix": None,
        "cpi_yoy_pct": None,
        "unemployment_pct": None,
        "hy_credit_spread": None,
        "source": "fallback",
    }
