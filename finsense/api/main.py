from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from finsense.config import settings
from finsense.data.providers import build_ticker_chart_payload, realized_forward_return
from finsense.models import AnalysisInput
from finsense.pipeline import FinSensePipeline
from finsense.user_config import load_user_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("finsense.api")

app = FastAPI(title=settings.app_name, version="2.0.0")
pipeline = FinSensePipeline()

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedupe_tickers(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        tk = str(item).strip().upper()
        if not tk or tk in seen:
            continue
        seen.add(tk)
        out.append(tk)
    return out


def _normalize_action(action_value: object) -> str:
    raw = str(action_value or "").strip().upper()
    if "." in raw:
        raw = raw.split(".")[-1]
    return raw if raw in {"BUY", "HOLD", "SELL"} else "HOLD"


def _future_business_date(anchor: str | None, business_days: int) -> str:
    if anchor:
        try:
            current = datetime.strptime(anchor, "%Y-%m-%d").date()
        except ValueError:
            current = datetime.now(timezone.utc).date()
    else:
        current = datetime.now(timezone.utc).date()
    remaining = max(0, int(business_days))
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current.isoformat()


def _parse_horizons(raw: str | None) -> list[int]:
    default = [10, 20, 30, 60, 90]
    if not raw:
        return default
    out: list[int] = []
    seen: set[int] = set()
    for part in str(raw).split(","):
        token = part.strip()
        if not token:
            continue
        try:
            v = int(token)
        except ValueError:
            continue
        if v < 5 or v > 1095 or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out or default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Horizon projection
# ---------------------------------------------------------------------------

def _horizon_projected_call(analysis: dict, horizon_days: int) -> dict:
    consensus = analysis.get("consensus", {}) or {}
    market = analysis.get("market", {}) or {}
    analogs = market.get("historical_analogs", []) or []

    h = max(5, int(horizon_days))
    h_norm = min(1.0, h / 252.0)
    base_score = float(consensus.get("weighted_score", 0.0))
    base_conf = float(consensus.get("confidence", 0.55))

    momentum = float(market.get("returns_20d", 0.0))
    news = float(market.get("news_sentiment", 0.0))
    growth = float(market.get("revenue_growth_yoy", 0.0))
    quality = float(market.get("roe", 0.0))
    volatility = float(market.get("realized_vol_20d", 0.02))
    analog_forward = (
        sum(float(a.get("forward_20d_return_pct", 0.0)) for a in analogs[:5]) / max(1, len(analogs[:5]))
    ) / 100.0
    fundamental = 0.6 * growth + 0.4 * quality

    short_weight = math.exp(-h / 55.0)
    long_weight = 1.0 - math.exp(-h / 140.0)
    vol_penalty = max(0.0, volatility - 0.018)

    projected_score = (
        base_score * (0.70 + 0.30 * math.exp(-h / 180.0))
        + momentum * (0.30 * short_weight)
        + news * (0.13 * math.exp(-h / 45.0))
        + analog_forward * (0.18 * long_weight)
        + fundamental * (0.20 * long_weight)
        - vol_penalty * (0.30 * (0.4 + h_norm))
    )
    projected_score = _clamp(projected_score, -1.0, 1.0)

    confidence = base_conf
    confidence *= 1.0 - min(0.22, (volatility * 1.9) * (0.25 + 0.75 * h_norm))
    confidence += min(0.06, abs(projected_score) * 0.10)
    confidence = _clamp(confidence, 0.45, 0.97)

    buy_thresh = 0.10 + 0.08 * h_norm
    action = "HOLD"
    if projected_score >= buy_thresh:
        action = "BUY"
    elif projected_score <= -buy_thresh:
        action = "SELL"

    position_pct = min(5.0, abs(projected_score) * confidence * 8.3)
    if action == "HOLD":
        position_pct = min(0.6, position_pct)

    note = (
        "short-term momentum/news weighted" if h <= 30
        else ("medium-term blend" if h <= 90 else "long-term fundamental/analog weighted")
    )
    return {
        "action": action,
        "confidence": round(confidence, 4),
        "position_pct": round(position_pct, 3),
        "weighted_score": round(projected_score, 4),
        "note": note,
    }


def _forward_horizon_rows(
    ticker: str, horizon_list: list[int], history_years: int,
    as_of_date: str | None, risk_budget_bps: int, benchmark: str,
) -> list[dict]:
    rows: list[dict] = []
    for h in horizon_list:
        req = AnalysisInput(
            ticker=ticker.upper(), horizon_days=h, history_years=history_years,
            as_of_date=as_of_date, risk_budget_bps=risk_budget_bps, benchmark=benchmark,
        )
        try:
            analysis = asdict(pipeline.run(req))
            proj = _horizon_projected_call(analysis, h)
            rows.append({
                "horizon_days": h,
                "predicted_for_date": _future_business_date(as_of_date, h),
                "action": proj["action"],
                "confidence": proj["confidence"],
                "position_pct": proj["position_pct"],
                "weighted_score": proj["weighted_score"],
                "horizon_note": proj["note"],
                "status": "ok",
            })
        except Exception as exc:
            rows.append({
                "horizon_days": h,
                "predicted_for_date": _future_business_date(as_of_date, h),
                "status": "error", "error": str(exc),
            })
    return rows


# ---------------------------------------------------------------------------
# Thesis & WaveEdge
# ---------------------------------------------------------------------------

def _thesis_from_analysis(analysis: dict) -> dict[str, str]:
    consensus = analysis.get("consensus", {})
    market = analysis.get("market", {})
    experts = analysis.get("experts", {})
    action = consensus.get("action", "HOLD")
    conf = float(consensus.get("confidence", 0.0))
    ret20d = float(market.get("returns_20d", 0.0)) * 100
    news = float(market.get("news_sentiment", 0.0))
    regime = market.get("macro_regime", "unknown")
    analogs = market.get("historical_analogs", [])
    data_quality = market.get("data_quality", "unknown")

    analog_text = "No strong historical analog found."
    if analogs:
        first = analogs[0]
        matched = ", ".join(first.get("matching_factors", [])) or "return profile only"
        analog_text = (
            f"Closest analog: {first.get('event_date')} where similar setup "
            f"led to {first.get('forward_20d_return_pct')}% over next 20 trading days. "
            f"Matched factors: {matched}."
        )

    quant_note = "; ".join((experts.get("wall_street_quant", {}) or {}).get("rationale", [])[:2])
    fundamental_note = "; ".join((experts.get("harvard_fundamental", {}) or {}).get("rationale", [])[:2])
    ml_note = "; ".join((experts.get("stanford_ml", {}) or {}).get("rationale", [])[:2])

    thesis = (
        f"{action} bias with {conf:.1%} confidence. 20-day move: {ret20d:.2f}% "
        f"in a {regime} regime. News sentiment: {news:+.2f}. {analog_text}"
    )
    if data_quality == "mock":
        thesis = "⚠️ MOCK DATA — " + thesis

    return {
        "thesis": thesis,
        "catalyst_risks": (
            "Upside catalysts: improving earnings trajectory, supportive macro shift, positive news flow. "
            "Downside risks: volatility shock, weak guidance, negative event risk."
        ),
        "repeatability_view": (
            "Pattern repeat probability moderate. Similar setups historically show direction tendencies "
            "but execution should respect risk caps and confirmation."
        ),
        "quant_view": quant_note,
        "fundamental_view": fundamental_note,
        "ml_view": ml_note,
    }


def _compute_wave_edge(analysis: dict) -> dict:
    market = analysis.get("market", {})
    consensus = analysis.get("consensus", {})
    analogs = market.get("historical_analogs", []) or []

    momentum = float(market.get("returns_20d", 0.0)) * 100.0
    news = float(market.get("news_sentiment", 0.0)) * 30.0
    conf = float(consensus.get("confidence", 0.0)) * 100.0
    vol_pen = float(market.get("realized_vol_20d", 0.0)) * 100.0
    consensus_bias = float(consensus.get("weighted_score", 0.0)) * 90.0
    regime = str(market.get("macro_regime", "disinflation"))

    analog_forward = 0.0
    if analogs:
        analog_forward = sum(float(a.get("forward_20d_return_pct", 0.0)) for a in analogs[:5]) / max(1, len(analogs[:5]))

    regime_adj = {"growth": 8.0, "disinflation": 5.0, "inflation": -4.0, "slowdown": -9.0}.get(regime, 0.0)

    score = (
        0.26 * momentum + 0.21 * analog_forward + 0.24 * consensus_bias
        + 0.14 * news + 0.10 * (conf - 50.0) - 0.17 * max(0.0, vol_pen - 2.5)
        + regime_adj
    )
    score = _clamp(score, -100.0, 100.0)
    up_prob = _clamp(50.0 + 0.45 * score, 1.0, 99.0)

    if score >= 35:
        label = "Strong Bullish Edge"
    elif score >= 12:
        label = "Moderate Bullish Edge"
    elif score <= -35:
        label = "Strong Bearish Edge"
    elif score <= -12:
        label = "Moderate Bearish Edge"
    else:
        label = "Balanced / Neutral"

    return {
        "name": "WaveEdge Score",
        "ticker": market.get("ticker"),
        "score": round(score, 2),
        "up_probability_pct": round(up_prob, 2),
        "label": label,
        "components": {
            "momentum_20d_pct": round(momentum, 2),
            "analog_forward_avg_pct": round(analog_forward, 2),
            "consensus_bias": round(consensus_bias, 2),
            "news": round(news, 2),
            "confidence": round(conf - 50.0, 2),
            "vol_penalty": round(max(0.0, vol_pen - 2.5), 2),
            "regime": round(regime_adj, 2),
        },
    }


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    horizon_days: int = Field(default=90, ge=5, le=1095)
    history_years: int = Field(default=10, ge=1, le=30)
    as_of_date: str | None = Field(default=None)
    risk_budget_bps: int = Field(default=150, ge=25, le=1000)
    benchmark: str = Field(default="SPY", min_length=1, max_length=10)


class BacktestRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)
    as_of_date: str = Field(description="YYYY-MM-DD")
    horizon_days: int = Field(default=20, ge=5, le=252)
    history_years: int = Field(default=10, ge=1, le=30)
    risk_budget_bps: int = Field(default=150, ge=25, le=1000)
    benchmark: str = Field(default="SPY", min_length=1, max_length=10)
    hold_band_pct: float = Field(default=2.0, ge=0.0, le=10.0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def dashboard():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name, "version": "2.0.0"}


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    request = AnalysisInput(
        ticker=req.ticker.upper(), horizon_days=req.horizon_days,
        history_years=req.history_years, as_of_date=req.as_of_date,
        risk_budget_bps=req.risk_budget_bps, benchmark=req.benchmark.upper(),
    )
    return asdict(pipeline.run(request))


@app.get("/user-config")
def user_config():
    return load_user_config()


@app.get("/analyze/watchlist")
def analyze_watchlist():
    cfg = load_user_config()
    tickers = _dedupe_tickers([str(t).upper() for t in cfg.get("tickers", []) if str(t).strip()])
    horizon_days = int(cfg.get("horizon_days", 90))
    history_years = int(cfg.get("history_years", 10))
    as_of_date = str(cfg.get("as_of_date", "")).strip() or None
    risk_budget_bps = int(cfg.get("risk_budget_bps", 150))
    benchmark = str(cfg.get("benchmark", "SPY")).upper()

    def _run_single(ticker: str) -> tuple[str, dict]:
        req = AnalysisInput(
            ticker=ticker, horizon_days=horizon_days, history_years=history_years,
            as_of_date=as_of_date, risk_budget_bps=risk_budget_bps, benchmark=benchmark,
        )
        return ticker, asdict(pipeline.run(req))

    results: dict[str, dict] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(tickers)))) as pool:
        futs = {pool.submit(_run_single, t): t for t in tickers}
        for f in as_completed(futs):
            t = futs[f]
            try:
                tk, a = f.result(timeout=60)
                results[tk] = a
            except Exception as exc:
                errors[t] = str(exc)

    analyses = [results[t] for t in tickers if t in results]
    return {"count": len(analyses), "tickers": tickers, "as_of_date": as_of_date, "results": analyses, "errors": errors}


@app.get("/dashboard/watchlist")
def dashboard_watchlist():
    payload = analyze_watchlist()
    rows: list[dict] = []
    for item in payload.get("results", []):
        consensus = item.get("consensus", {})
        market = item.get("market", {})
        risk = item.get("risk", {})
        we = _compute_wave_edge(item)
        rows.append({
            "ticker": market.get("ticker"),
            "action": _normalize_action(consensus.get("action")),
            "confidence": round(float(consensus.get("confidence", 0.0)), 4),
            "position_pct": round(float(consensus.get("recommended_position_pct", 0.0)), 3),
            "weighted_score": round(float(consensus.get("weighted_score", 0.0)), 4),
            "price": round(float(market.get("price", 0.0)), 2),
            "returns_1d": round(float(market.get("returns_1d", 0.0)) * 100, 2),
            "returns_20d": round(float(market.get("returns_20d", 0.0)) * 100, 2),
            "beta": round(float(market.get("beta_to_market", 1.0)), 3),
            "realized_vol_20d": round(float(market.get("realized_vol_20d", 0.0)) * 100, 2),
            "rsi_14": round(float(market.get("rsi_14", 50.0)), 1),
            "news_sentiment": round(float(market.get("news_sentiment", 0.0)), 3),
            "macro_regime": market.get("macro_regime", ""),
            "top_headline": (market.get("news_headlines") or [""])[0],
            "wave_edge_score": we.get("score", 0.0),
            "wave_edge_up_prob": we.get("up_probability_pct", 50.0),
            "data_quality": market.get("data_quality", "unknown"),
            "liquidity_score": round(float(risk.get("liquidity_score", 0.5)), 2),
        })
    rows.sort(key=lambda x: x["confidence"], reverse=True)
    actions = {"BUY": 0, "HOLD": 0, "SELL": 0}
    for r in rows:
        a = r["action"]
        if a in actions:
            actions[a] += 1
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "action_distribution": actions,
        "avg_confidence": round(sum(r["confidence"] for r in rows) / max(1, len(rows)), 4),
        "avg_position_pct": round(sum(r["position_pct"] for r in rows) / max(1, len(rows)), 3),
        "rows": rows,
        "errors": payload.get("errors", {}),
    }


@app.get("/dashboard/ticker/{ticker}")
def dashboard_ticker_detail(ticker: str):
    cfg = load_user_config()
    req = AnalysisInput(
        ticker=ticker.upper(),
        horizon_days=int(cfg.get("horizon_days", 90)),
        history_years=int(cfg.get("history_years", 10)),
        as_of_date=str(cfg.get("as_of_date", "")).strip() or None,
        risk_budget_bps=int(cfg.get("risk_budget_bps", 150)),
        benchmark=str(cfg.get("benchmark", "SPY")).upper(),
    )
    analysis = asdict(pipeline.run(req))
    market = analysis.get("market", {})
    chart = build_ticker_chart_payload(ticker.upper(), history_years=max(2, req.history_years), analog_rows=market.get("historical_analogs", []))
    return {
        "ticker": ticker.upper(),
        "as_of_date": req.as_of_date,
        "analysis": analysis,
        "thesis_pack": _thesis_from_analysis(analysis),
        "wave_edge_indicator": _compute_wave_edge(analysis),
        "matching_method": {
            "description": "Analogs ranked by weighted distance across 20D momentum, 20D volatility, and 20D benchmark backdrop.",
            "weights": {"momentum_20d": 0.55, "volatility_20d": 0.30, "benchmark_20d": 0.15},
        },
        "sources": market.get("data_sources", []),
        "news_items": market.get("news_items", []),
        "historical_analogs": market.get("historical_analogs", []),
        "technicals": market.get("technicals", {}),
        "chart": chart,
    }


@app.get("/dashboard/forward-horizons/{ticker}")
def dashboard_forward_horizons(ticker: str, horizons: str | None = None):
    cfg = load_user_config()
    return {
        "ticker": ticker.upper(),
        "rows": _forward_horizon_rows(
            ticker.upper(), _parse_horizons(horizons),
            int(cfg.get("history_years", 10)),
            str(cfg.get("as_of_date", "")).strip() or None,
            int(cfg.get("risk_budget_bps", 150)),
            str(cfg.get("benchmark", "SPY")).upper(),
        ),
    }


@app.get("/dashboard/forward-horizons")
def dashboard_forward_horizons_all(tickers: str | None = None, horizons: str | None = None):
    cfg = load_user_config()
    from_query = [t.strip().upper() for t in str(tickers or "").split(",") if t.strip()]
    base = [str(t).upper() for t in cfg.get("tickers", []) if str(t).strip()]
    universe = _dedupe_tickers(from_query or base)
    hl = _parse_horizons(horizons)
    hy = int(cfg.get("history_years", 10))
    aod = str(cfg.get("as_of_date", "")).strip() or None
    rb = int(cfg.get("risk_budget_bps", 150))
    bm = str(cfg.get("benchmark", "SPY")).upper()
    items = [{"ticker": tk, "rows": _forward_horizon_rows(tk, hl, hy, aod, rb, bm)} for tk in universe]
    return {"tickers": universe, "horizons": hl, "as_of_date": aod or datetime.now(timezone.utc).strftime("%Y-%m-%d"), "items": items}


def _is_hit(action: str, fwd_pct: float, band: float) -> bool:
    action = _normalize_action(action)
    if action == "BUY":
        return fwd_pct > 0
    if action == "SELL":
        return fwd_pct < 0
    return abs(fwd_pct) <= band


@app.post("/validate/backtest")
def validate_backtest(req: BacktestRequest):
    cfg = load_user_config()
    tickers = _dedupe_tickers(req.tickers or [str(t) for t in cfg.get("tickers", [])])
    benchmark = req.benchmark.upper()
    rows: list[dict] = []
    for ticker in tickers:
        request = AnalysisInput(
            ticker=ticker, horizon_days=req.horizon_days, history_years=req.history_years,
            as_of_date=req.as_of_date, risk_budget_bps=req.risk_budget_bps, benchmark=benchmark,
        )
        try:
            analysis = asdict(pipeline.run(request))
            action = _normalize_action(analysis.get("consensus", {}).get("action", "HOLD"))
            conf = float(analysis.get("consensus", {}).get("confidence", 0.0))
            realized = realized_forward_return(ticker, req.as_of_date, req.horizon_days)
            if not realized:
                rows.append({"ticker": ticker, "status": "no_forward_data", "predicted_action": action, "predicted_confidence": round(conf, 4)})
                continue
            fwd = float(realized.get("forward_return_pct", 0.0))
            hit = _is_hit(action, fwd, req.hold_band_pct)
            rows.append({
                "ticker": ticker, "status": "ok", "predicted_action": action,
                "predicted_confidence": round(conf, 4),
                "as_of_used": realized.get("as_of_used"),
                "evaluation_end_date": realized.get("end_date"),
                "horizon_days_used": int(realized.get("horizon_days_used", req.horizon_days)),
                "partial_horizon": bool(realized.get("partial_horizon", False)),
                "start_price": realized.get("start_price"),
                "end_price": realized.get("end_price"),
                "realized_forward_return_pct": round(fwd, 4),
                "hit": hit,
            })
        except Exception as exc:
            rows.append({"ticker": ticker, "status": "error", "error": str(exc)})
    valid = [r for r in rows if r.get("status") == "ok"]
    hits = sum(1 for r in valid if r.get("hit"))
    return {
        "as_of_date": req.as_of_date, "horizon_days": req.horizon_days,
        "benchmark": benchmark, "hold_band_pct": req.hold_band_pct,
        "count_total": len(rows), "count_scored": len(valid),
        "hit_rate": round(hits / len(valid), 4) if valid else None,
        "rows": rows,
    }
