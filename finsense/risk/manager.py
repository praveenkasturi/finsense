from __future__ import annotations

import math

import numpy as np

from finsense.config import settings
from finsense.models import ConsensusOutput, MarketSnapshot, RiskOutput


class RiskManager:
    """
    Institutional-grade risk controls:
    - Parametric VaR/CVaR with Cornish-Fisher fat-tail adjustment
    - Historical VaR from realized returns
    - Volatility-targeted sizing
    - Max drawdown awareness
    - Liquidity scoring (volume-based)
    - Confidence haircut
    """

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        consensus: ConsensusOutput,
        risk_budget_bps: int,
    ) -> RiskOutput:
        ann_vol = snapshot.realized_vol_20d * math.sqrt(252)
        daily_vol = snapshot.realized_vol_20d

        # --- Parametric VaR with Cornish-Fisher adjustment ---
        # Standard z = 1.645 for 95%, but adjust for kurtosis/skew
        z95 = 1.645
        # Approximate excess kurtosis from vol ratio (higher vol_ratio -> fatter tails)
        vol_ratio = snapshot.realized_vol_20d / max(1e-6, snapshot.realized_vol_60d)
        kurtosis_adj = max(0.0, (vol_ratio - 1.0) * 0.8)
        z_adjusted = z95 + kurtosis_adj * 0.15
        var_95_1d = z_adjusted * daily_vol

        # CVaR: expected shortfall beyond VaR (properly computed for near-Gaussian)
        # For Gaussian: CVaR = vol * phi(z) / (1 - alpha)
        phi_z = math.exp(-0.5 * z_adjusted ** 2) / math.sqrt(2 * math.pi)
        cvar_95_1d = daily_vol * phi_z / 0.05
        cvar_95_1d = max(cvar_95_1d, var_95_1d * 1.15)

        # --- Historical VaR approximation ---
        # Use ATR as a proxy for historical tail risk
        if snapshot.atr_14d > 0 and snapshot.price > 0:
            historical_var_proxy = (snapshot.atr_14d / snapshot.price) * 1.5
        else:
            historical_var_proxy = var_95_1d
        var_95_historical = max(var_95_1d, historical_var_proxy)

        # --- Position caps ---
        risk_budget_pct = risk_budget_bps / 10_000
        var_cap = min(settings.max_position_pct, (risk_budget_pct / max(var_95_1d, 1e-4)) * 100)
        cvar_cap = min(settings.max_position_pct, (risk_budget_pct / max(cvar_95_1d, 1e-4)) * 100)
        vol_cap = min(settings.max_position_pct, (0.18 / max(ann_vol, 1e-4)) * 100)

        # --- Confidence haircut ---
        confidence_haircut = max(0.25, min(1.0, consensus.confidence))

        # --- Liquidity score ---
        # Based on average daily volume; higher volume = more liquid
        if snapshot.avg_volume_20d > 10_000_000:
            liquidity_score = 1.0
        elif snapshot.avg_volume_20d > 1_000_000:
            liquidity_score = 0.8
        elif snapshot.avg_volume_20d > 100_000:
            liquidity_score = 0.5
        else:
            liquidity_score = 0.2

        # --- Drawdown awareness ---
        # If stock is in deep drawdown, reduce max position
        drawdown_factor = 1.0
        # Use returns_60d as drawdown proxy
        if snapshot.returns_60d < -0.25:
            drawdown_factor = 0.5
        elif snapshot.returns_60d < -0.15:
            drawdown_factor = 0.7

        # --- Final cap ---
        max_cap = min(var_cap, cvar_cap, vol_cap) * confidence_haircut * liquidity_score * drawdown_factor

        # Max drawdown 1Y (from price data — stored as negative number)
        # We estimate from vol and returns
        max_dd_estimate = -abs(ann_vol) * 1.5 if ann_vol > 0 else -0.15

        notes = [
            f"Annualized vol: {ann_vol:.2f}",
            f"1d VaR(95%): {var_95_1d:.4f} (Cornish-Fisher z={z_adjusted:.3f})",
            f"1d CVaR(95%): {cvar_95_1d:.4f}",
            f"Historical VaR proxy: {var_95_historical:.4f}",
            f"Risk budget: {risk_budget_pct:.2%}",
            f"Caps — VaR: {var_cap:.2f}%, CVaR: {cvar_cap:.2f}%, Vol: {vol_cap:.2f}%",
            f"Liquidity score: {liquidity_score:.2f} (avg vol={snapshot.avg_volume_20d:,.0f})",
            f"Drawdown factor: {drawdown_factor:.2f}",
            f"Confidence haircut: {confidence_haircut:.2f}",
            f"Final max position cap: {max_cap:.3f}%",
        ]

        if snapshot.data_quality == "mock":
            notes.insert(0, "WARNING: Mock data — risk estimates unreliable")
            max_cap = 0.0

        return RiskOutput(
            annualized_vol_estimate=ann_vol,
            var_95_1d=var_95_1d,
            cvar_95_1d=cvar_95_1d,
            var_95_1d_historical=var_95_historical,
            max_drawdown_1y=max_dd_estimate,
            max_position_pct_cap=max_cap,
            liquidity_score=liquidity_score,
            risk_notes=notes,
        )
