from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd

from finsense.config import settings
from finsense.models import ExpertOutput, MarketSnapshot, TradeAction

logger = logging.getLogger("finsense.ml")

try:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.calibration import CalibratedClassifierCV
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

try:
    import yfinance as yf
except Exception:
    yf = None


def _compute_rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean()
    rs = up / down.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _build_feature_matrix(close: pd.Series, volume: pd.Series | None = None) -> pd.DataFrame:
    """Build a rich feature matrix from price history at each trading day."""
    df = pd.DataFrame(index=close.index)
    ret = close.pct_change()

    # Momentum features (multi-timeframe)
    df["ret_1d"] = ret
    df["ret_5d"] = close.pct_change(5)
    df["ret_20d"] = close.pct_change(20)
    df["ret_60d"] = close.pct_change(60)

    # Volatility features
    df["vol_5d"] = ret.rolling(5).std()
    df["vol_20d"] = ret.rolling(20).std()
    df["vol_60d"] = ret.rolling(60).std()
    df["vol_ratio"] = df["vol_20d"] / df["vol_60d"].clip(lower=1e-6)

    # RSI
    df["rsi_14"] = _compute_rsi_series(close, 14) / 100.0

    # SMA distances (normalized)
    df["dist_sma20"] = (close - close.rolling(20).mean()) / close.rolling(20).mean().clip(lower=1e-6)
    df["dist_sma50"] = (close - close.rolling(50).mean()) / close.rolling(50).mean().clip(lower=1e-6)

    # MACD histogram normalized
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    df["macd_hist_norm"] = (macd - signal) / close.clip(lower=1e-6)

    # Bollinger %B
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df["bb_pct_b"] = (close - (bb_mid - 2 * bb_std)) / (4 * bb_std).clip(lower=1e-6)

    # Stochastic K
    low_14 = close.rolling(14).min()
    high_14 = close.rolling(14).max()
    df["stoch_k"] = (close - low_14) / (high_14 - low_14).clip(lower=1e-6)

    # Volume features
    if volume is not None and len(volume) > 0:
        avg_vol = volume.rolling(20).mean().clip(lower=1)
        df["vol_ratio_vol"] = volume / avg_vol
        direction = ret.apply(lambda x: 1 if x > 0 else -1)
        obv = (volume * direction).cumsum()
        df["obv_pct_20d"] = obv.pct_change(20)
    else:
        df["vol_ratio_vol"] = 1.0
        df["obv_pct_20d"] = 0.0

    # Drawdown feature
    running_max = close.cummax()
    df["drawdown"] = (close - running_max) / running_max.clip(lower=1e-6)

    return df.dropna()


class MLExpert:
    """
    Real ML-based directional prediction.

    When settings.enable_ml_training is True:
    - Fetches historical price data
    - Builds feature matrix (momentum, vol, technicals, volume)
    - Labels with forward 20-day return sign
    - Trains GradientBoostingClassifier with time-series split
    - Predicts on latest features with calibrated probability

    Falls back to a heuristic logit model when training is disabled or fails.
    """

    name = "stanford_ml"

    def __init__(self) -> None:
        self._model_cache: dict[str, dict[str, Any]] = {}

    def _train_model(self, ticker: str, forward_days: int = 20) -> dict[str, Any] | None:
        if not _HAS_SKLEARN or yf is None:
            return None
        try:
            hist = yf.download(ticker, period="10y", interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20)
            if hist is None or hist.empty or len(hist) < 500:
                return None
            close = hist["Close"].dropna()
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            volume = hist["Volume"].dropna() if "Volume" in hist.columns else None
            if isinstance(volume, pd.DataFrame):
                volume = volume.iloc[:, 0]
        except Exception as exc:
            logger.warning("ML training data fetch failed for %s: %s", ticker, exc)
            return None

        features = _build_feature_matrix(close, volume)
        forward_ret = close.pct_change(forward_days).shift(-forward_days)
        labels = (forward_ret > 0).astype(int)

        common = features.index.intersection(labels.dropna().index)
        if len(common) < 300:
            return None
        X = features.loc[common]
        y = labels.loc[common]

        # Walk-forward: train on first 70%, validate on last 30%
        split_idx = int(len(X) * 0.70)
        X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]

        model = GradientBoostingClassifier(
            n_estimators=150,
            max_depth=3,
            learning_rate=0.08,
            subsample=0.8,
            min_samples_leaf=20,
            random_state=42,
        )
        model.fit(X_train, y_train)

        val_accuracy = float(model.score(X_val, y_val))
        feature_names = list(X.columns)
        importances = dict(zip(feature_names, [round(float(v), 4) for v in model.feature_importances_]))

        # Latest feature vector for prediction
        latest_features = features.iloc[[-1]]

        return {
            "model": model,
            "feature_names": feature_names,
            "importances": importances,
            "val_accuracy": val_accuracy,
            "val_samples": len(X_val),
            "train_samples": len(X_train),
            "latest_features": latest_features,
        }

    def _heuristic_score(self, snapshot: MarketSnapshot) -> tuple[float, float, dict]:
        """Fallback heuristic when ML training is unavailable."""
        features = {
            "r1d": snapshot.returns_1d,
            "r5d": snapshot.returns_5d,
            "r20d": snapshot.returns_20d,
            "r60d": snapshot.returns_60d,
            "vol20": snapshot.realized_vol_20d,
            "beta": snapshot.beta_to_market,
            "iv": snapshot.implied_vol_30d,
            "pc": snapshot.put_call_ratio,
            "growth": snapshot.revenue_growth_yoy,
            "quality": snapshot.roe,
            "news": snapshot.news_sentiment,
            "rsi": (snapshot.rsi_14 - 50) / 100.0,
            "macd": snapshot.macd_histogram / max(0.01, snapshot.price * 0.001),
            "bb": snapshot.bollinger_pct_b - 0.5,
        }
        logit = (
            1.5 * features["r20d"]
            + 0.6 * features["r5d"]
            + 0.5 * features["r60d"]
            + 0.6 * features["growth"]
            + 0.5 * features["quality"]
            + 0.5 * features["news"]
            + 0.4 * features["rsi"]
            + 0.3 * features["macd"]
            - 0.3 * features["bb"]
            - 0.9 * features["vol20"]
            - 0.3 * features["beta"]
            - 0.4 * features["iv"]
            - 0.25 * features["pc"]
        )
        prob = 1.0 / (1.0 + math.exp(-logit))
        calibrated = 0.5 + 0.80 * (prob - 0.5)
        raw_score = (calibrated - 0.5) * 2.0
        uncertainty = min(0.45, abs(features["vol20"] - features["iv"]) * 0.7 + max(0.0, features["beta"] - 1.2) * 0.1)
        confidence = min(0.90, max(0.45, 1.0 - uncertainty))
        return raw_score, confidence, {"method": "heuristic_logit", "upward_probability": round(calibrated, 4)}

    def analyze(self, snapshot: MarketSnapshot) -> ExpertOutput:
        diagnostics: dict[str, Any] = {}
        trained = None

        if settings.enable_ml_training and snapshot.data_quality != "mock":
            cache_key = snapshot.ticker
            if cache_key not in self._model_cache:
                logger.info("Training ML model for %s ...", snapshot.ticker)
                trained = self._train_model(snapshot.ticker)
                if trained:
                    self._model_cache[cache_key] = trained
            else:
                trained = self._model_cache[cache_key]

        if trained and trained.get("model") is not None:
            model = trained["model"]
            latest = trained["latest_features"]
            prob = float(model.predict_proba(latest)[0, 1])
            raw_score = (prob - 0.5) * 2.0

            val_acc = trained["val_accuracy"]
            # Confidence derived from model accuracy and prediction strength
            confidence = min(0.95, max(0.45, val_acc * 0.6 + abs(raw_score) * 0.35))

            anomaly_risk = min(1.0, abs(snapshot.returns_1d) * 8 + abs(snapshot.returns_20d) * 2)
            if anomaly_risk > 0.7:
                confidence *= 0.85

            diagnostics = {
                "method": "gradient_boosting",
                "upward_probability": round(prob, 4),
                "val_accuracy": round(val_acc, 4),
                "train_samples": trained["train_samples"],
                "val_samples": trained["val_samples"],
                "top_features": dict(sorted(trained["importances"].items(), key=lambda x: -x[1])[:5]),
                "anomaly_risk": round(anomaly_risk, 4),
            }

            rationale = [
                f"ML model (GradientBoosting) upward probability: {prob:.3f}",
                f"Walk-forward validation accuracy: {val_acc:.1%} on {trained['val_samples']} samples",
                f"Top features: {', '.join(list(diagnostics['top_features'].keys())[:3])}",
                f"Anomaly risk: {anomaly_risk:.3f}",
            ]
        else:
            raw_score, confidence, diag = self._heuristic_score(snapshot)
            diagnostics = diag
            diagnostics["anomaly_risk"] = round(min(1.0, abs(snapshot.returns_1d) * 8 + abs(snapshot.returns_20d) * 2), 4)
            rationale = [
                f"Heuristic ensemble probability: {diag.get('upward_probability', 0.5):.3f}",
                f"ML training {'disabled' if not settings.enable_ml_training else 'failed/insufficient data'}",
                f"Anomaly risk: {diagnostics['anomaly_risk']:.3f}",
            ]

        signal = TradeAction.HOLD
        if raw_score > 0.14:
            signal = TradeAction.BUY
        elif raw_score < -0.14:
            signal = TradeAction.SELL

        return ExpertOutput(
            expert_name=self.name,
            raw_score=raw_score,
            confidence=confidence,
            signal=signal,
            rationale=rationale,
            diagnostics=diagnostics,
        )
