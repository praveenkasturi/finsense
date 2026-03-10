from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TradeAction(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


@dataclass(slots=True)
class AnalysisInput:
    ticker: str
    horizon_days: int = 90
    history_years: int = 10
    as_of_date: str | None = None
    risk_budget_bps: int = 150
    benchmark: str = "SPY"


@dataclass(slots=True)
class MarketSnapshot:
    ticker: str
    price: float
    # Multi-timeframe returns
    returns_1d: float
    returns_5d: float
    returns_20d: float
    returns_60d: float
    # Volatility
    realized_vol_20d: float
    realized_vol_60d: float
    atr_14d: float
    # Market relationship
    beta_to_market: float
    correlation_to_market: float
    # Fundamentals
    pe_ratio: float
    forward_pe: float
    pb_ratio: float
    ps_ratio: float
    roe: float
    roa: float
    debt_to_equity: float
    current_ratio: float
    revenue_growth_yoy: float
    eps_growth_yoy: float
    gross_margin: float
    operating_margin: float
    free_cash_flow_yield: float
    dividend_yield: float
    # Options / Flow
    put_call_ratio: float
    implied_vol_30d: float
    iv_rv_spread: float
    short_interest_pct: float
    dark_pool_ratio: float
    # Volume
    avg_volume_20d: float
    volume_ratio: float
    obv_trend: float
    # Technicals (pre-computed)
    rsi_14: float
    macd_value: float
    macd_signal: float
    macd_histogram: float
    sma_20: float
    sma_50: float
    sma_200: float
    ema_12: float
    ema_26: float
    bollinger_upper: float
    bollinger_lower: float
    bollinger_pct_b: float
    stochastic_k: float
    stochastic_d: float
    adx_14: float
    # Sentiment
    news_sentiment: float
    news_sentiment_source: str
    news_headlines: list[str]
    news_items: list[dict[str, Any]]
    # Analogs
    historical_analogs: list[dict[str, Any]]
    # Macro
    macro_regime: str
    macro_details: dict[str, Any]
    # Technical dict for display
    technicals: dict[str, float]
    # Meta
    data_sources: list[str]
    data_quality: str
    data_warnings: list[str]


@dataclass(slots=True)
class ExpertOutput:
    expert_name: str
    raw_score: float
    confidence: float
    signal: TradeAction
    rationale: list[str]
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConsensusOutput:
    action: TradeAction
    confidence: float
    weighted_score: float
    expert_weights: dict[str, float]
    recommended_position_pct: float
    disagreement_index: float
    rationale: list[str]


@dataclass(slots=True)
class RiskOutput:
    annualized_vol_estimate: float
    var_95_1d: float
    cvar_95_1d: float
    var_95_1d_historical: float
    max_drawdown_1y: float
    max_position_pct_cap: float
    liquidity_score: float
    risk_notes: list[str]


@dataclass(slots=True)
class FullAnalysis:
    timestamp_utc: str
    input: AnalysisInput
    market: MarketSnapshot
    experts: dict[str, ExpertOutput]
    consensus: ConsensusOutput
    risk: RiskOutput

    @staticmethod
    def now_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()
