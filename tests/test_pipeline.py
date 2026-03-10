from finsense.data.providers import MockMarketDataProvider
from finsense.models import AnalysisInput, TradeAction
from finsense.pipeline import FinSensePipeline


def test_pipeline_returns_valid_output() -> None:
    pipeline = FinSensePipeline(data_provider=MockMarketDataProvider())
    result = pipeline.run(AnalysisInput(ticker="AAPL", horizon_days=120, risk_budget_bps=200))

    assert result.input.ticker == "AAPL"
    assert result.consensus.action in {TradeAction.BUY, TradeAction.HOLD, TradeAction.SELL}
    assert 0.0 <= result.consensus.confidence <= 1.0
    assert 0.0 <= result.consensus.recommended_position_pct <= 25.0
    assert "wall_street_quant" in result.experts
    assert "harvard_fundamental" in result.experts
    assert "stanford_ml" in result.experts


def test_mock_data_blocks_position() -> None:
    pipeline = FinSensePipeline(data_provider=MockMarketDataProvider())
    result = pipeline.run(AnalysisInput(ticker="TSLA"))
    assert result.market.data_quality == "mock"
    assert result.consensus.recommended_position_pct == 0.0
    assert result.risk.max_position_pct_cap == 0.0


def test_all_experts_produce_rationale() -> None:
    pipeline = FinSensePipeline(data_provider=MockMarketDataProvider())
    result = pipeline.run(AnalysisInput(ticker="MSFT"))
    for name, expert in result.experts.items():
        assert len(expert.rationale) > 0, f"{name} has no rationale"
        assert expert.confidence >= 0.0


def test_risk_output_fields() -> None:
    pipeline = FinSensePipeline(data_provider=MockMarketDataProvider())
    result = pipeline.run(AnalysisInput(ticker="NVDA"))
    assert result.risk.annualized_vol_estimate > 0
    assert result.risk.var_95_1d > 0
    assert result.risk.cvar_95_1d >= result.risk.var_95_1d
    assert 0.0 <= result.risk.liquidity_score <= 1.0
