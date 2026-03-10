from __future__ import annotations

import logging

from finsense.data.providers import MarketDataProvider, ResilientMarketDataProvider
from finsense.engine.consensus import ConsensusEngine
from finsense.experts.fundamental import FundamentalExpert
from finsense.experts.ml import MLExpert
from finsense.experts.quant import QuantExpert
from finsense.models import AnalysisInput, FullAnalysis
from finsense.risk.manager import RiskManager

logger = logging.getLogger("finsense.pipeline")


class FinSensePipeline:
    def __init__(self, data_provider: MarketDataProvider | None = None) -> None:
        self.data_provider = data_provider or ResilientMarketDataProvider()
        self.quant = QuantExpert()
        self.fundamental = FundamentalExpert()
        self.ml = MLExpert()
        self.consensus_engine = ConsensusEngine()
        self.risk_manager = RiskManager()

    def run(self, request: AnalysisInput) -> FullAnalysis:
        logger.info("Analyzing %s (horizon=%dd, as_of=%s)", request.ticker, request.horizon_days, request.as_of_date)

        snapshot = self.data_provider.get_snapshot(
            request.ticker,
            request.benchmark,
            request.history_years,
            request.as_of_date,
        )

        quant_out = self.quant.analyze(snapshot)
        fundamental_out = self.fundamental.analyze(snapshot)
        ml_out = self.ml.analyze(snapshot)

        experts = {
            quant_out.expert_name: quant_out,
            fundamental_out.expert_name: fundamental_out,
            ml_out.expert_name: ml_out,
        }
        consensus = self.consensus_engine.combine(snapshot, experts)
        risk = self.risk_manager.evaluate(snapshot, consensus, request.risk_budget_bps)

        # Clamp position to risk cap
        consensus.recommended_position_pct = min(
            consensus.recommended_position_pct,
            risk.max_position_pct_cap,
        )

        logger.info(
            "%s -> %s (conf=%.2f, pos=%.2f%%, quality=%s)",
            request.ticker,
            consensus.action.value,
            consensus.confidence,
            consensus.recommended_position_pct,
            snapshot.data_quality,
        )

        return FullAnalysis(
            timestamp_utc=FullAnalysis.now_timestamp(),
            input=request,
            market=snapshot,
            experts=experts,
            consensus=consensus,
            risk=risk,
        )
