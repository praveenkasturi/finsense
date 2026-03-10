from __future__ import annotations

import math

from finsense.models import ExpertOutput, MarketSnapshot, TradeAction


class FundamentalExpert:
    """
    Harvard-style fundamental analysis with:
    - Multi-metric valuation (PE, PB, PS, FCF yield, forward PE)
    - Profitability quality (ROE, ROA, margins)
    - Balance sheet strength (leverage, current ratio)
    - Growth quality (revenue, EPS, margin trajectory)
    - Moat & durability scoring
    - Macro & news overlays
    """

    name = "harvard_fundamental"

    def analyze(self, snapshot: MarketSnapshot) -> ExpertOutput:
        # --- Valuation composite ---
        pe_score = max(-0.5, min(0.5, (18.0 - snapshot.pe_ratio) / 36.0))
        fwd_pe_score = max(-0.5, min(0.5, (16.0 - snapshot.forward_pe) / 32.0))
        pb_score = max(-0.5, min(0.5, (3.0 - snapshot.pb_ratio) / 6.0))
        ps_score = max(-0.5, min(0.5, (5.0 - snapshot.ps_ratio) / 10.0))
        fcf_score = max(-0.3, min(0.3, snapshot.free_cash_flow_yield * 3.0))
        valuation = 0.25 * pe_score + 0.20 * fwd_pe_score + 0.20 * pb_score + 0.15 * ps_score + 0.20 * fcf_score

        # --- Profitability quality ---
        roe_score = max(0.0, min(0.5, snapshot.roe))
        roa_score = max(0.0, min(0.3, snapshot.roa))
        gross_m = max(0.0, min(0.5, snapshot.gross_margin - 0.2))
        op_m = max(0.0, min(0.4, snapshot.operating_margin))
        profitability = 0.30 * roe_score + 0.20 * roa_score + 0.25 * gross_m + 0.25 * op_m

        # --- Balance sheet ---
        leverage_penalty = max(0.0, snapshot.debt_to_equity - 1.0) * 0.3
        liquidity_bonus = max(0.0, min(0.15, (snapshot.current_ratio - 1.0) * 0.1))
        balance_sheet = liquidity_bonus - leverage_penalty

        # --- Growth quality ---
        growth_quality = (
            0.50 * max(-0.3, min(0.5, snapshot.revenue_growth_yoy))
            + 0.50 * max(-0.3, min(0.5, snapshot.eps_growth_yoy))
        )

        # --- Moat score ---
        moat = (
            0.30 * max(0.0, profitability)
            + 0.30 * max(0.0, growth_quality)
            + 0.20 * max(0.0, min(0.3, snapshot.gross_margin - 0.35))
            + 0.20 * (1.0 - min(1.0, max(0.0, leverage_penalty)))
        )

        # --- DCF proxy ---
        dcf_proxy_return = (
            0.40 * growth_quality + 0.35 * profitability + 0.25 * valuation - 0.10 * leverage_penalty
        ) * 100.0

        # --- Dividend yield bonus ---
        div_bonus = min(0.05, snapshot.dividend_yield * 0.5)

        # --- News overlay ---
        news_overlay = 0.10 * snapshot.news_sentiment

        # --- Macro overlay ---
        macro_overlay = {
            "growth": 0.10,
            "disinflation": 0.08,
            "inflation": -0.05,
            "slowdown": -0.12,
        }.get(snapshot.macro_regime, 0.0)

        # --- Composite ---
        raw_score = math.tanh(
            0.30 * valuation
            + 0.20 * profitability
            + 0.10 * balance_sheet
            + 0.15 * growth_quality
            + 0.10 * moat
            + 0.02 * div_bonus
            + macro_overlay
            + news_overlay
        )

        confidence = min(0.93, 0.52 + abs(raw_score) * 0.40)

        signal = TradeAction.HOLD
        if raw_score > 0.16:
            signal = TradeAction.BUY
        elif raw_score < -0.16:
            signal = TradeAction.SELL

        rationale = [
            f"Valuation composite: {valuation:.4f} (PE={pe_score:+.3f}, FwdPE={fwd_pe_score:+.3f}, PB={pb_score:+.3f}, PS={ps_score:+.3f}, FCF={fcf_score:+.3f})",
            f"Profitability: {profitability:.4f} (ROE={snapshot.roe:.2f}, ROA={snapshot.roa:.2f}, Gross={snapshot.gross_margin:.2f}, OpMgn={snapshot.operating_margin:.2f})",
            f"Balance sheet: {balance_sheet:+.4f} (D/E={snapshot.debt_to_equity:.2f}, CR={snapshot.current_ratio:.2f})",
            f"Growth quality: {growth_quality:+.4f} (Rev={snapshot.revenue_growth_yoy:.1%}, EPS={snapshot.eps_growth_yoy:.1%})",
            f"Moat score: {moat:.4f}, DCF proxy return: {dcf_proxy_return:.1f}%",
            f"Macro regime ({snapshot.macro_regime}): {macro_overlay:+.2f}, News: {snapshot.news_sentiment:+.3f}",
        ]

        return ExpertOutput(
            expert_name=self.name,
            raw_score=raw_score,
            confidence=confidence,
            signal=signal,
            rationale=rationale,
            diagnostics={
                "valuation": round(valuation, 4),
                "profitability": round(profitability, 4),
                "balance_sheet": round(balance_sheet, 4),
                "growth_quality": round(growth_quality, 4),
                "moat": round(moat, 4),
                "dcf_proxy_return_pct": round(dcf_proxy_return, 2),
                "macro_regime": snapshot.macro_regime,
                "news_sentiment": snapshot.news_sentiment,
            },
        )
