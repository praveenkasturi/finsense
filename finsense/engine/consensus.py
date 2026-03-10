from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from finsense.config import settings
from finsense.models import ConsensusOutput, ExpertOutput, MarketSnapshot, TradeAction

logger = logging.getLogger("finsense.consensus")

ACCURACY_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "models_cache" / "expert_accuracy.json"


class ConsensusEngine:
    """
    Combines expert signals with:
    - Regime-aware base weights
    - Confidence-scaled dynamic weighting
    - Historical accuracy tracking (when available)
    - Disagreement diagnostics
    """

    def __init__(self) -> None:
        self._accuracy_history: dict[str, dict[str, float]] = self._load_accuracy()

    def _load_accuracy(self) -> dict[str, dict[str, float]]:
        if ACCURACY_LOG_PATH.exists():
            try:
                return json.loads(ACCURACY_LOG_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def save_accuracy(self) -> None:
        ACCURACY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ACCURACY_LOG_PATH.write_text(json.dumps(self._accuracy_history, indent=2), encoding="utf-8")

    def record_hit(self, expert_name: str, hit: bool) -> None:
        if expert_name not in self._accuracy_history:
            self._accuracy_history[expert_name] = {"hits": 0.0, "total": 0.0}
        self._accuracy_history[expert_name]["total"] += 1.0
        if hit:
            self._accuracy_history[expert_name]["hits"] += 1.0
        self.save_accuracy()

    def _expert_accuracy(self, expert_name: str) -> float | None:
        entry = self._accuracy_history.get(expert_name)
        if entry and entry.get("total", 0) >= 10:
            return entry["hits"] / entry["total"]
        return None

    def _regime_weights(self, regime: str) -> dict[str, float]:
        if regime in {"inflation", "slowdown"}:
            return {"wall_street_quant": 0.45, "harvard_fundamental": 0.35, "stanford_ml": 0.20}
        if regime in {"growth", "disinflation"}:
            return {"wall_street_quant": 0.28, "harvard_fundamental": 0.32, "stanford_ml": 0.40}
        return {"wall_street_quant": 0.34, "harvard_fundamental": 0.33, "stanford_ml": 0.33}

    def combine(self, snapshot: MarketSnapshot, experts: dict[str, ExpertOutput]) -> ConsensusOutput:
        base_weights = self._regime_weights(snapshot.macro_regime)
        weighted = 0.0
        total_weight = 0.0
        rationale: list[str] = []
        adjusted_weights: dict[str, float] = {}

        for expert_key, output in experts.items():
            w = base_weights.get(expert_key, 0.0) * (0.6 + 0.4 * output.confidence)

            # Accuracy-adjusted: boost experts with proven track record
            acc = self._expert_accuracy(expert_key)
            if acc is not None:
                acc_bonus = max(0.5, min(1.5, acc / 0.55))
                w *= acc_bonus
                rationale.append(f"{expert_key}: historical accuracy={acc:.1%}, bonus={acc_bonus:.2f}")

            adjusted_weights[expert_key] = w
            weighted += w * output.raw_score
            total_weight += w
            rationale.append(
                f"{expert_key}: score={output.raw_score:.3f}, confidence={output.confidence:.3f}, weight={w:.3f}"
            )

        weighted_score = weighted / total_weight if total_weight > 0 else 0.0

        # Normalize weights for display
        norm = sum(adjusted_weights.values()) or 1.0
        adjusted_weights = {k: round(v / norm, 4) for k, v in adjusted_weights.items()}

        # Disagreement analysis
        signals = [experts[k].signal for k in experts]
        unique_signals = len({s.value for s in signals})
        disagreement_index = (unique_signals - 1) / 2.0

        # Check if any expert has very different magnitude
        scores = [experts[k].raw_score for k in experts]
        score_spread = max(scores) - min(scores) if scores else 0
        if score_spread > 0.8:
            disagreement_index = min(1.0, disagreement_index + 0.2)

        # Confidence
        confidence = min(0.97, 0.55 + 0.35 * abs(weighted_score) + 0.10 * (1.0 - disagreement_index))
        if disagreement_index > 0.5:
            confidence *= 0.88
        if snapshot.data_quality == "mock":
            confidence *= 0.5
            rationale.append("WARNING: Mock data — confidence halved")

        # Action decision
        action = TradeAction.HOLD
        if weighted_score > 0.11 and confidence >= settings.confidence_threshold_buy:
            action = TradeAction.BUY
        elif weighted_score < -0.11 and confidence >= settings.confidence_threshold_sell:
            action = TradeAction.SELL

        # Position sizing
        proposed_position = min(settings.max_position_pct, abs(weighted_score) * confidence * 8.5)
        if action == TradeAction.HOLD:
            proposed_position = min(0.5, proposed_position)
        if snapshot.data_quality == "mock":
            proposed_position = 0.0

        rationale.append(f"Regime: {snapshot.macro_regime} | Disagreement: {disagreement_index:.3f}")
        rationale.append(f"Final weighted score: {weighted_score:.4f}")
        rationale.append(f"Data quality: {snapshot.data_quality}")

        return ConsensusOutput(
            action=action,
            confidence=confidence,
            weighted_score=weighted_score,
            expert_weights=adjusted_weights,
            recommended_position_pct=proposed_position,
            disagreement_index=disagreement_index,
            rationale=rationale,
        )
