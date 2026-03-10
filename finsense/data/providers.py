from __future__ import annotations

import hashlib
import logging
import math
import random
from abc import ABC, abstractmethod
from functools import lru_cache

import numpy as np
import pandas as pd

from finsense.data.macro import classify_regime_from_bench, fetch_macro_context
from finsense.data.sentiment import fetch_newsapi_headlines, score_headlines, yfinance_news_sentiment
from finsense.models import MarketSnapshot
from finsense.user_config import load_user_config

try:
    import yfinance as yf
except Exception:
    yf = None

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logger = logging.getLogger("finsense.data")


class MarketDataProvider(ABC):
    @abstractmethod
    def get_snapshot(
        self,
        ticker: str,
        benchmark: str,
        history_years: int = 10,
        as_of_date: str | None = None,
    ) -> MarketSnapshot:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Technical indicator helpers (pure numpy/pandas, no external TA library)
# ---------------------------------------------------------------------------

def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean()
    rs = up / down.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    mask = plus_dm < minus_dm
    plus_dm[mask] = 0.0
    minus_dm[~mask] = 0.0
    atr = _compute_atr(high, low, close, period)
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr.clip(lower=1e-10))
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr.clip(lower=1e-10))
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).clip(lower=1e-10) * 100
    return dx.rolling(period).mean()


def _compute_stochastic(close: pd.Series, high: pd.Series, low: pd.Series, k_period: int = 14, d_period: int = 3) -> tuple[pd.Series, pd.Series]:
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low).clip(lower=1e-10)
    d = k.rolling(d_period).mean()
    return k, d


def _compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (volume * direction).cumsum()


def _compute_technicals(close: pd.Series, high: pd.Series | None = None, low: pd.Series | None = None, volume: pd.Series | None = None) -> dict[str, float]:
    if high is None:
        high = close * 1.005
    if low is None:
        low = close * 0.995
    if volume is None:
        volume = pd.Series(np.ones(len(close)) * 1e6, index=close.index)

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean() if len(close) >= 200 else close.expanding(50).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal

    bb_mid = sma20
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_pct_b = (close - bb_lower) / (bb_upper - bb_lower).clip(lower=1e-10)

    rsi = _compute_rsi(close, 14)
    atr = _compute_atr(high, low, close, 14)
    adx = _compute_adx(high, low, close, 14)
    stoch_k, stoch_d = _compute_stochastic(close, high, low)
    obv = _compute_obv(close, volume)

    def _last(s: pd.Series, default: float = 0.0) -> float:
        v = s.dropna()
        return float(v.iloc[-1]) if len(v) > 0 else default

    avg_vol_20 = float(volume.rolling(20).mean().dropna().iloc[-1]) if len(volume) >= 20 else float(volume.mean())
    vol_ratio = float(volume.iloc[-1]) / max(1.0, avg_vol_20)
    obv_vals = obv.dropna()
    obv_trend = 0.0
    if len(obv_vals) > 20:
        obv_trend = float(obv_vals.iloc[-1] - obv_vals.iloc[-20]) / max(1.0, abs(float(obv_vals.iloc[-20])))

    return {
        "sma_20": _last(sma20),
        "sma_50": _last(sma50),
        "sma_200": _last(sma200),
        "ema_12": _last(ema12),
        "ema_26": _last(ema26),
        "macd_value": _last(macd),
        "macd_signal": _last(macd_signal),
        "macd_histogram": _last(macd_hist),
        "rsi_14": _last(rsi, 50.0),
        "atr_14d": _last(atr),
        "adx_14": _last(adx, 25.0),
        "bollinger_upper": _last(bb_upper),
        "bollinger_lower": _last(bb_lower),
        "bollinger_pct_b": _last(bb_pct_b, 0.5),
        "stochastic_k": _last(stoch_k, 50.0),
        "stochastic_d": _last(stoch_d, 50.0),
        "avg_volume_20d": avg_vol_20,
        "volume_ratio": vol_ratio,
        "obv_trend": obv_trend,
    }


# ---------------------------------------------------------------------------
# Historical analog engine
# ---------------------------------------------------------------------------

def _historical_analogs(close: pd.Series, bench_close: pd.Series) -> list[dict]:
    if len(close) < 120 or len(bench_close) < 120:
        return []
    lookback, forward = 20, 20
    ret = close.pct_change().dropna()
    bench_ret = bench_close.pct_change().dropna()
    aligned = pd.concat([ret, bench_ret], axis=1, join="inner").dropna()
    aligned.columns = ["asset", "bench"]
    if len(aligned) < 120:
        return []

    target_ret20 = float(close.iloc[-1] / close.iloc[-1 - lookback] - 1.0)
    target_vol20 = float(ret.tail(lookback).std(ddof=1))
    target_bench20 = float(bench_close.iloc[-1] / bench_close.iloc[-1 - lookback] - 1.0)

    ret_scale = max(0.01, abs(target_ret20) * 0.6)
    vol_scale = max(0.003, target_vol20 * 0.5)
    bench_scale = max(0.01, abs(target_bench20) * 0.6)
    analogs: list[dict] = []

    for i in range(lookback, len(close) - forward - 1):
        if close.index[i] not in aligned.index:
            continue
        hist_return = float(close.iloc[i] / close.iloc[i - lookback] - 1.0)
        future_return = float(close.iloc[i + forward] / close.iloc[i] - 1.0)
        hist_window = ret.iloc[i - lookback: i]
        hist_vol20 = float(hist_window.std(ddof=1))

        bench_idx = bench_close.index.get_indexer([close.index[i]], method="nearest")[0]
        if bench_idx < lookback:
            continue
        hist_bench20 = float(bench_close.iloc[bench_idx] / bench_close.iloc[bench_idx - lookback] - 1.0)

        ret_delta = hist_return - target_ret20
        vol_delta = hist_vol20 - target_vol20
        bench_delta = hist_bench20 - target_bench20
        distance = (
            abs(ret_delta) / ret_scale * 0.55
            + abs(vol_delta) / vol_scale * 0.30
            + abs(bench_delta) / bench_scale * 0.15
        )

        matching_factors: list[str] = []
        if abs(ret_delta) <= ret_scale * 0.7:
            matching_factors.append("20D momentum")
        if abs(vol_delta) <= vol_scale * 0.8:
            matching_factors.append("20D volatility regime")
        if abs(bench_delta) <= bench_scale * 0.8:
            matching_factors.append("market backdrop")

        analogs.append({
            "event_date": str(close.index[i].date()),
            "lookback_return_pct": round(hist_return * 100, 2),
            "lookback_vol_pct": round(hist_vol20 * 100, 2),
            "benchmark_20d_return_pct": round(hist_bench20 * 100, 2),
            "forward_20d_return_pct": round(future_return * 100, 2),
            "setup_start_price": round(float(close.iloc[i - lookback]), 2),
            "event_price": round(float(close.iloc[i]), 2),
            "forward_20d_price": round(float(close.iloc[i + forward]), 2),
            "matching_factors": matching_factors,
            "factor_deltas": {
                "delta_momentum_pct": round(ret_delta * 100, 2),
                "delta_vol_pct": round(vol_delta * 100, 2),
                "delta_benchmark_pct": round(bench_delta * 100, 2),
            },
            "distance": distance,
        })

    analogs.sort(key=lambda x: float(x["distance"]))
    top = analogs[:5]
    for row in top:
        matched = row.get("matching_factors", [])
        d = row.get("factor_deltas", {})
        row["note"] = ("Matched on: " + ", ".join(matched)) if matched else "Weak match: mostly return proximity only"
        row["why_matched"] = (
            f"Momentum delta {d.get('delta_momentum_pct', 0)}%, "
            f"volatility delta {d.get('delta_vol_pct', 0)}%, "
            f"benchmark delta {d.get('delta_benchmark_pct', 0)}%."
        )
        row.pop("distance", None)
    return top


# ---------------------------------------------------------------------------
# Yahoo enhanced provider
# ---------------------------------------------------------------------------

class YahooEnhancedProvider(MarketDataProvider):

    @lru_cache(maxsize=256)
    def _ticker_info(self, ticker: str) -> dict:
        if yf is None:
            return {}
        try:
            return yf.Ticker(ticker).info or {}
        except Exception:
            return {}

    @lru_cache(maxsize=512)
    def _price_history(self, ticker: str, history_years: int, as_of_date: str = "") -> tuple | None:
        if yf is None:
            return None
        try:
            if as_of_date:
                end_dt = pd.Timestamp(as_of_date).normalize() + pd.Timedelta(days=1)
                start_dt = end_dt - pd.Timedelta(days=max(370, history_years * 366))
                hist = yf.download(ticker, start=start_dt.strftime("%Y-%m-%d"), end=end_dt.strftime("%Y-%m-%d"), interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20)
            else:
                hist = yf.download(ticker, period=f"{max(1, history_years)}y", interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20)
            if hist is None or hist.empty or "Close" not in hist.columns:
                return None

            def _col(name: str) -> pd.Series:
                s = hist[name].dropna() if name in hist.columns else pd.Series(dtype=float)
                if isinstance(s, pd.DataFrame):
                    s = s.iloc[:, 0]
                return s

            close = _col("Close")
            high = _col("High") if "High" in hist.columns else close * 1.005
            low = _col("Low") if "Low" in hist.columns else close * 0.995
            volume = _col("Volume") if "Volume" in hist.columns else pd.Series(np.ones(len(close)) * 1e6, index=close.index)
            return close, high, low, volume
        except Exception:
            return None

    def get_snapshot(
        self,
        ticker: str,
        benchmark: str,
        history_years: int = 10,
        as_of_date: str | None = None,
    ) -> MarketSnapshot:
        as_of = (as_of_date or "").strip()
        result = self._price_history(ticker, history_years, as_of)
        bench_result = self._price_history(benchmark, history_years, as_of)
        if result is None or bench_result is None:
            raise RuntimeError(f"Insufficient market history for {ticker}")
        close, high, low, volume = result
        bench_close = bench_result[0]
        if len(close) < 25 or len(bench_close) < 25:
            raise RuntimeError(f"Insufficient market history for {ticker}")

        warnings: list[str] = []
        sources: list[str] = ["yfinance-prices"]
        ret = close.pct_change().dropna()
        bench_ret = bench_close.pct_change().dropna()
        aligned = pd.concat([ret, bench_ret], axis=1, join="inner").dropna()
        aligned.columns = ["asset", "bench"]

        # Multi-timeframe returns
        returns_1d = float(ret.iloc[-1]) if len(ret) > 0 else 0.0
        returns_5d = float(close.iloc[-1] / close.iloc[-6] - 1.0) if len(close) > 6 else 0.0
        returns_20d = float(close.iloc[-1] / close.iloc[-21] - 1.0) if len(close) > 21 else 0.0
        returns_60d = float(close.iloc[-1] / close.iloc[-61] - 1.0) if len(close) > 61 else 0.0

        # Volatility
        realized_vol_20d = float(ret.tail(20).std(ddof=1))
        realized_vol_60d = float(ret.tail(60).std(ddof=1)) if len(ret) >= 60 else realized_vol_20d

        # Beta & correlation
        bench_var = float(aligned["bench"].var(ddof=1))
        cov_val = float(np.cov(aligned["asset"], aligned["bench"], ddof=1)[0, 1]) if len(aligned) > 2 else 0.0
        beta_to_market = cov_val / bench_var if bench_var > 1e-12 else 1.0
        corr = float(aligned["asset"].corr(aligned["bench"])) if len(aligned) > 20 else 0.5

        # Fundamentals from yfinance
        info = self._ticker_info(ticker)
        sources.append("yfinance-fundamentals")
        pe_ratio = float(info.get("trailingPE") or info.get("forwardPE") or 20.0)
        forward_pe = float(info.get("forwardPE") or pe_ratio)
        pb_ratio = float(info.get("priceToBook") or 3.0)
        ps_ratio = float(info.get("priceToSalesTrailing12Months") or 5.0)
        roe = float(info.get("returnOnEquity") or 0.12)
        roa = float(info.get("returnOnAssets") or 0.06)
        debt_to_equity = float(info.get("debtToEquity") or 100.0) / 100.0
        current_ratio = float(info.get("currentRatio") or 1.5)
        revenue_growth_yoy = float(info.get("revenueGrowth") or 0.08)
        eps_growth_yoy = float(info.get("earningsGrowth") or 0.10)
        gross_margin = float(info.get("grossMargins") or 0.40)
        operating_margin = float(info.get("operatingMargins") or 0.15)
        fcf = float(info.get("freeCashflow") or 0)
        market_cap = float(info.get("marketCap") or 1)
        fcf_yield = (fcf / market_cap) if market_cap > 0 else 0.0
        dividend_yield = float(info.get("dividendYield") or 0.0)
        short_pct = float(info.get("shortPercentOfFloat") or 0.0)

        # News sentiment — try NewsAPI first, fall back to yfinance
        cfg = load_user_config()
        news_api_key = cfg.get("api_keys", {}).get("news", "")
        if news_api_key:
            news_sentiment, news_items, news_headlines = fetch_newsapi_headlines(ticker, news_api_key)
            sentiment_source = "newsapi"
            sources.append("newsapi-sentiment")
        else:
            try:
                yf_ticker = yf.Ticker(ticker) if yf else None
            except Exception:
                yf_ticker = None
            if yf_ticker:
                news_sentiment, news_items, news_headlines = yfinance_news_sentiment(yf_ticker)
                sentiment_source = "yfinance-news"
                sources.append("yfinance-news")
            else:
                news_sentiment, news_items, news_headlines = 0.0, [], []
                sentiment_source = "none"
                warnings.append("No news source available")

        # Options sentiment — approximate from news if real data unavailable
        put_call_ratio = float(max(0.5, min(1.5, 1.0 - 0.25 * news_sentiment)))
        implied_vol_30d = max(0.12, min(0.90, realized_vol_20d * math.sqrt(252)))
        iv_rv_spread = implied_vol_30d - (realized_vol_20d * math.sqrt(252))
        dark_pool_ratio = 0.45
        warnings.append("Options flow approximated from sentiment (no real options data)")

        # Technicals
        techs = _compute_technicals(close, high, low, volume)

        # Macro regime
        fred_key = cfg.get("api_keys", {}).get("fred", "")
        if fred_key:
            macro_details = fetch_macro_context(fred_key)
            macro_regime = macro_details.get("regime", "disinflation")
            sources.append("FRED-macro")
        else:
            bench_20d = float(bench_close.iloc[-1] / bench_close.iloc[-21] - 1.0) if len(bench_close) > 21 else 0.0
            bench_vol = float(bench_ret.tail(20).std(ddof=1))
            macro_regime = classify_regime_from_bench(bench_20d, bench_vol)
            macro_details = {"regime": macro_regime, "source": "benchmark-proxy"}

        # Historical analogs
        analogs = _historical_analogs(close, bench_close)

        # Max drawdown 1Y
        close_1y = close.tail(252)
        running_max = close_1y.cummax()
        drawdown = (close_1y - running_max) / running_max
        max_dd_1y = float(drawdown.min()) if len(drawdown) > 0 else 0.0

        return MarketSnapshot(
            ticker=ticker.upper(),
            price=float(close.iloc[-1]),
            returns_1d=returns_1d,
            returns_5d=returns_5d,
            returns_20d=returns_20d,
            returns_60d=returns_60d,
            realized_vol_20d=max(1e-4, realized_vol_20d),
            realized_vol_60d=max(1e-4, realized_vol_60d),
            atr_14d=techs["atr_14d"],
            beta_to_market=float(beta_to_market),
            correlation_to_market=float(corr),
            pe_ratio=pe_ratio,
            forward_pe=forward_pe,
            pb_ratio=pb_ratio,
            ps_ratio=ps_ratio,
            roe=roe,
            roa=roa,
            debt_to_equity=debt_to_equity,
            current_ratio=current_ratio,
            revenue_growth_yoy=revenue_growth_yoy,
            eps_growth_yoy=eps_growth_yoy,
            gross_margin=gross_margin,
            operating_margin=operating_margin,
            free_cash_flow_yield=fcf_yield,
            dividend_yield=dividend_yield,
            put_call_ratio=float(max(0.2, min(4.0, put_call_ratio))),
            implied_vol_30d=float(max(0.05, min(2.0, implied_vol_30d))),
            iv_rv_spread=iv_rv_spread,
            short_interest_pct=short_pct,
            dark_pool_ratio=dark_pool_ratio,
            avg_volume_20d=techs["avg_volume_20d"],
            volume_ratio=techs["volume_ratio"],
            obv_trend=techs["obv_trend"],
            rsi_14=techs["rsi_14"],
            macd_value=techs["macd_value"],
            macd_signal=techs["macd_signal"],
            macd_histogram=techs["macd_histogram"],
            sma_20=techs["sma_20"],
            sma_50=techs["sma_50"],
            sma_200=techs["sma_200"],
            ema_12=techs["ema_12"],
            ema_26=techs["ema_26"],
            bollinger_upper=techs["bollinger_upper"],
            bollinger_lower=techs["bollinger_lower"],
            bollinger_pct_b=techs["bollinger_pct_b"],
            stochastic_k=techs["stochastic_k"],
            stochastic_d=techs["stochastic_d"],
            adx_14=techs["adx_14"],
            news_sentiment=news_sentiment,
            news_sentiment_source=sentiment_source,
            news_headlines=news_headlines[:8],
            news_items=news_items[:10],
            historical_analogs=analogs,
            macro_regime=macro_regime,
            macro_details=macro_details,
            technicals=techs,
            data_sources=sources,
            data_quality="live",
            data_warnings=warnings,
        )


# ---------------------------------------------------------------------------
# Mock provider (explicit, never silent)
# ---------------------------------------------------------------------------

class MockMarketDataProvider(MarketDataProvider):
    def get_snapshot(
        self,
        ticker: str,
        benchmark: str,
        history_years: int = 10,
        as_of_date: str | None = None,
    ) -> MarketSnapshot:
        seed = int(hashlib.sha256(f"{ticker}:{benchmark}".encode()).hexdigest(), 16) % (10**8)
        rng = random.Random(seed)
        price = rng.uniform(25, 500)
        r1d = rng.uniform(-0.04, 0.04)
        r5d = rng.uniform(-0.08, 0.08)
        r20d = rng.uniform(-0.15, 0.15)
        r60d = rng.uniform(-0.20, 0.20)
        vol20 = rng.uniform(0.10, 0.65)
        vol60 = rng.uniform(0.10, 0.55)
        beta = rng.uniform(0.4, 1.8)
        pe = rng.uniform(8, 45)
        rsi = 50 + r20d * 80

        return MarketSnapshot(
            ticker=ticker.upper(), price=price,
            returns_1d=r1d, returns_5d=r5d, returns_20d=r20d, returns_60d=r60d,
            realized_vol_20d=vol20, realized_vol_60d=vol60, atr_14d=price * vol20 * 0.06,
            beta_to_market=beta, correlation_to_market=rng.uniform(0.3, 0.9),
            pe_ratio=pe, forward_pe=pe * 0.9, pb_ratio=rng.uniform(0.8, 12),
            ps_ratio=rng.uniform(1, 20), roe=rng.uniform(-0.1, 0.4), roa=rng.uniform(-0.05, 0.2),
            debt_to_equity=rng.uniform(0.0, 2.5), current_ratio=rng.uniform(0.5, 3.0),
            revenue_growth_yoy=rng.uniform(-0.2, 0.5), eps_growth_yoy=rng.uniform(-0.3, 0.7),
            gross_margin=rng.uniform(0.2, 0.8), operating_margin=rng.uniform(0.0, 0.4),
            free_cash_flow_yield=rng.uniform(-0.02, 0.08), dividend_yield=rng.uniform(0, 0.04),
            put_call_ratio=rng.uniform(0.5, 1.8), implied_vol_30d=rng.uniform(0.12, 0.8),
            iv_rv_spread=rng.uniform(-0.1, 0.1), short_interest_pct=rng.uniform(0, 0.15),
            dark_pool_ratio=rng.uniform(0.2, 0.7),
            avg_volume_20d=rng.uniform(1e5, 5e7), volume_ratio=rng.uniform(0.5, 2.0), obv_trend=rng.uniform(-0.3, 0.3),
            rsi_14=max(10, min(90, rsi)), macd_value=r20d * price / 10, macd_signal=r20d * price / 12,
            macd_histogram=r20d * price / 60,
            sma_20=price * 0.99, sma_50=price * 0.97, sma_200=price * 0.92,
            ema_12=price * 0.995, ema_26=price * 0.985,
            bollinger_upper=price * 1.04, bollinger_lower=price * 0.96, bollinger_pct_b=0.5 + r20d,
            stochastic_k=50 + r20d * 100, stochastic_d=50 + r20d * 80, adx_14=rng.uniform(10, 50),
            news_sentiment=rng.uniform(-0.5, 0.5), news_sentiment_source="mock",
            news_headlines=[f"{ticker} mock headline"], news_items=[],
            historical_analogs=[], macro_regime=rng.choice(["growth", "inflation", "disinflation", "slowdown"]),
            macro_details={"regime": "unknown", "source": "mock"},
            technicals={}, data_sources=["mock-provider"],
            data_quality="mock",
            data_warnings=["ALL DATA IS MOCK — do not trade on this signal"],
        )


# ---------------------------------------------------------------------------
# Resilient provider — falls back but WARNS clearly
# ---------------------------------------------------------------------------

class ResilientMarketDataProvider(MarketDataProvider):
    def __init__(self) -> None:
        self.yahoo = YahooEnhancedProvider()
        self.mock = MockMarketDataProvider()

    def get_snapshot(
        self,
        ticker: str,
        benchmark: str,
        history_years: int = 10,
        as_of_date: str | None = None,
    ) -> MarketSnapshot:
        try:
            return self.yahoo.get_snapshot(ticker, benchmark, history_years=history_years, as_of_date=as_of_date)
        except Exception as exc:
            logger.warning("Live data failed for %s, using MOCK: %s", ticker, exc)
            return self.mock.get_snapshot(ticker, benchmark, history_years=history_years, as_of_date=as_of_date)


# ---------------------------------------------------------------------------
# Utility functions for API layer
# ---------------------------------------------------------------------------

def realized_forward_return(ticker: str, as_of_date: str, horizon_days: int) -> dict | None:
    provider = YahooEnhancedProvider()
    result = provider._price_history(ticker, history_years=15, as_of_date="")
    if result is None:
        return None
    close = result[0]
    if len(close) < 40:
        return None
    try:
        ts = pd.Timestamp(as_of_date).normalize()
    except Exception:
        return None
    idx = close.index.searchsorted(ts, side="right") - 1
    if idx < 1 or idx >= len(close):
        return None
    forward_days_available = max(0, (len(close) - 1) - idx)
    if forward_days_available < 1:
        return None
    used_days = min(max(1, int(horizon_days)), forward_days_available)
    end_idx = idx + used_days
    start_price = float(close.iloc[idx])
    end_price = float(close.iloc[end_idx])
    ret_pct = ((end_price / start_price) - 1.0) * 100.0 if start_price else 0.0
    return {
        "as_of_used": str(close.index[idx].date()),
        "end_date": str(close.index[end_idx].date()),
        "horizon_days_requested": max(1, int(horizon_days)),
        "horizon_days_used": used_days,
        "partial_horizon": used_days < max(1, int(horizon_days)),
        "start_price": round(start_price, 4),
        "end_price": round(end_price, 4),
        "forward_return_pct": round(ret_pct, 4),
    }


def build_ticker_chart_payload(ticker: str, history_years: int = 2, analog_rows: list[dict] | None = None) -> dict:
    analog_rows = analog_rows or []
    analog_dates = [str(a.get("event_date", "")) for a in analog_rows if a.get("event_date")]
    analog_rank_map = {str(a.get("event_date", "")): idx + 1 for idx, a in enumerate(analog_rows) if a.get("event_date")}
    provider = YahooEnhancedProvider()
    result = provider._price_history(ticker, max(1, history_years))
    if result is None or len(result[0]) < 60:
        close = pd.Series(
            np.linspace(100, 120, 200) + np.sin(np.linspace(0, 16, 200)) * 3,
            index=pd.date_range(end=pd.Timestamp.today(), periods=200, freq="B"),
        )
        source = "synthetic-fallback"
    else:
        close = result[0]
        source = "yfinance-prices"

    sma20 = close.rolling(20).mean().bfill()
    sma50 = close.rolling(50).mean().bfill()
    dates = [str(d.date()) for d in close.index]
    analog_set = set(analog_dates)
    analog_markers = [float(v) if d in analog_set else None for d, v in zip(dates, close.values)]
    analog_marker_labels = [f"A{analog_rank_map[d]} - {d}" if d in analog_rank_map else None for d in dates]

    return {
        "source": source,
        "dates": dates,
        "close": [round(float(v), 4) for v in close.values],
        "sma20": [round(float(v), 4) for v in sma20.values],
        "sma50": [round(float(v), 4) for v in sma50.values],
        "analog_markers": analog_markers,
        "analog_marker_labels": analog_marker_labels,
    }
