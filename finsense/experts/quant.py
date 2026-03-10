from __future__ import annotations

import math

from finsense.models import ExpertOutput, MarketSnapshot, TradeAction


class QuantExpert:
    """
    Wall Street quant-style multi-factor alpha model.
    Uses value, quality, momentum (multi-timeframe), volatility,
    sentiment, institutional flow, AND all computed technicals
    (RSI, MACD, Bollinger, SMA crossovers, ADX, Stochastic, volume).
    """

    name = "wall_street_quant"

    def analyze(self, snapshot: MarketSnapshot) -> ExpertOutput:
        # --- Factor scores ---
        value_factor = max(0.0, (20.0 - snapshot.pe_ratio) / 20.0) * 0.4 + max(0.0, (3.0 - snapshot.pb_ratio) / 3.0) * 0.3 + max(0.0, snapshot.free_cash_flow_yield * 5.0) * 0.3
        quality_factor = (
            0.35 * max(0.0, snapshot.roe)
            + 0.25 * max(0.0, snapshot.roa)
            + 0.20 * max(0.0, snapshot.gross_margin)
            + 0.20 * max(0.0, min(1.0, 1.0 - snapshot.debt_to_equity / 3.0))
        )

        # Multi-timeframe momentum (cross-sectional style: 12M-1M is classic)
        short_mom = snapshot.returns_5d
        mid_mom = snapshot.returns_20d
        long_mom = snapshot.returns_60d
        momentum_factor = 0.20 * short_mom + 0.40 * mid_mom + 0.40 * long_mom

        # Volatility penalty
        vol_penalty = snapshot.realized_vol_20d

        # Options sentiment
        sentiment_factor = (1.0 - snapshot.put_call_ratio) * 0.25
        institutional_flow = (snapshot.dark_pool_ratio - 0.45) * 0.35

        # --- Technical overlay scores ---
        # RSI: oversold (<30) = bullish, overbought (>70) = bearish
        rsi = snapshot.rsi_14
        rsi_score = 0.0
        if rsi < 30:
            rsi_score = 0.4 * (30 - rsi) / 30
        elif rsi > 70:
            rsi_score = -0.4 * (rsi - 70) / 30
        else:
            rsi_score = 0.1 * (50 - rsi) / 50

        # MACD histogram: positive and rising = bullish
        macd_score = math.tanh(snapshot.macd_histogram / max(0.01, snapshot.price * 0.001)) * 0.3

        # SMA trend alignment: price > SMA20 > SMA50 > SMA200 = strong uptrend
        sma_alignment = 0.0
        if snapshot.price > snapshot.sma_20:
            sma_alignment += 0.15
        if snapshot.sma_20 > snapshot.sma_50:
            sma_alignment += 0.10
        if snapshot.sma_50 > snapshot.sma_200 and snapshot.sma_200 > 0:
            sma_alignment += 0.10
        if snapshot.price < snapshot.sma_200 and snapshot.sma_200 > 0:
            sma_alignment -= 0.20

        # Bollinger %B: near 0 = oversold, near 1 = overbought
        bb_score = 0.0
        if snapshot.bollinger_pct_b < 0.1:
            bb_score = 0.2
        elif snapshot.bollinger_pct_b > 0.9:
            bb_score = -0.15

        # Stochastic: oversold (<20) = buy, overbought (>80) = sell
        stoch_score = 0.0
        if snapshot.stochastic_k < 20 and snapshot.stochastic_d < 25:
            stoch_score = 0.15
        elif snapshot.stochastic_k > 80 and snapshot.stochastic_d > 75:
            stoch_score = -0.15

        # ADX: strong trend (>25) amplifies direction signals
        adx_multiplier = 1.0
        if snapshot.adx_14 > 30:
            adx_multiplier = 1.25
        elif snapshot.adx_14 < 15:
            adx_multiplier = 0.75

        # Volume confirmation: above-average volume on up moves = bullish
        volume_score = 0.0
        if snapshot.volume_ratio > 1.3 and snapshot.returns_1d > 0:
            volume_score = 0.10
        elif snapshot.volume_ratio > 1.3 and snapshot.returns_1d < 0:
            volume_score = -0.10
        volume_score += snapshot.obv_trend * 0.15

        # Short interest: high short interest = squeeze potential but also bearish pressure
        short_score = 0.0
        if snapshot.short_interest_pct > 0.15:
            short_score = -0.10
        elif snapshot.short_interest_pct > 0.10:
            short_score = -0.05

        # --- Composite ---
        factor_score = (
            0.15 * value_factor
            + 0.15 * quality_factor
            + 0.18 * momentum_factor
            + 0.08 * sentiment_factor
            + 0.05 * institutional_flow
            + 0.10 * rsi_score
            + 0.08 * macd_score
            + 0.06 * sma_alignment
            + 0.04 * bb_score
            + 0.03 * stoch_score
            + 0.04 * volume_score
            + 0.02 * short_score
        )

        # Apply ADX multiplier to directional components
        factor_score *= adx_multiplier

        beta_adjusted_alpha = factor_score - 0.10 * (snapshot.beta_to_market - 1.0)
        risk_adjusted_score = beta_adjusted_alpha / max(0.05, vol_penalty)
        normalized_score = math.tanh(risk_adjusted_score)
        confidence = min(0.95, 0.50 + abs(normalized_score) * 0.45)

        # Higher ADX = more confident in directional call
        if snapshot.adx_14 > 25:
            confidence = min(0.95, confidence + 0.03)

        signal = TradeAction.HOLD
        if normalized_score > 0.18:
            signal = TradeAction.BUY
        elif normalized_score < -0.18:
            signal = TradeAction.SELL

        rationale = [
            f"Factor composite: {factor_score:.4f} (value={value_factor:.3f}, quality={quality_factor:.3f}, momentum={momentum_factor:.3f})",
            f"Technical overlay: RSI={rsi:.1f} ({rsi_score:+.3f}), MACD hist={macd_score:+.3f}, SMA align={sma_alignment:+.3f}",
            f"Bollinger %B={snapshot.bollinger_pct_b:.2f} ({bb_score:+.3f}), Stochastic K={snapshot.stochastic_k:.1f} ({stoch_score:+.3f})",
            f"ADX={snapshot.adx_14:.1f} (multiplier={adx_multiplier:.2f}), Volume ratio={snapshot.volume_ratio:.2f} ({volume_score:+.3f})",
            f"Beta-adjusted alpha: {beta_adjusted_alpha:.4f}, Vol-normalized: {risk_adjusted_score:.4f}",
            f"Put/call={snapshot.put_call_ratio:.2f}, Short interest={snapshot.short_interest_pct:.1%}",
        ]

        return ExpertOutput(
            expert_name=self.name,
            raw_score=normalized_score,
            confidence=confidence,
            signal=signal,
            rationale=rationale,
            diagnostics={
                "factor_components": {
                    "value": round(value_factor, 4),
                    "quality": round(quality_factor, 4),
                    "momentum": round(momentum_factor, 4),
                    "rsi": round(rsi_score, 4),
                    "macd": round(macd_score, 4),
                    "sma_alignment": round(sma_alignment, 4),
                    "bollinger": round(bb_score, 4),
                    "stochastic": round(stoch_score, 4),
                    "volume": round(volume_score, 4),
                },
                "adx_multiplier": adx_multiplier,
                "beta": snapshot.beta_to_market,
                "realized_vol_20d": snapshot.realized_vol_20d,
                "implied_vol_30d": snapshot.implied_vol_30d,
            },
        )
