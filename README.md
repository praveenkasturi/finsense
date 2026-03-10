# FinSense Stock Intelligence v2.0

Institutional-grade stock intelligence platform — a complete rebuild of InSense with every major gap addressed.

## What Changed from InSense

| Area | InSense v1 | FinSense v2 |
|------|-----------|-------------|
| **Technicals** | Computed but never used by any expert | RSI, MACD, Bollinger, Stochastic, ADX, SMA crossovers, volume all wired into Quant Expert |
| **ML Expert** | Hardcoded linear weights | Real GradientBoosting trained on 10Y walk-forward data per ticker |
| **Sentiment** | 20-word bag-of-words | 46 weighted phrases with negation detection + News API integration |
| **Macro** | 2 variables, 4 thresholds | FRED API (yields, VIX, CPI, unemployment, credit spreads) + HMM-style regime classifier |
| **Fundamentals** | PE + ROE only | PE, Forward PE, PB, PS, FCF yield, ROE, ROA, margins, current ratio, dividend yield |
| **Risk** | Gaussian VaR × 1.25 | Cornish-Fisher VaR, proper CVaR, liquidity scoring, drawdown awareness |
| **Mock fallback** | Silent — trades on fake data | Explicit `data_quality` flag, zero position sizing on mock data |
| **Consensus** | Static regime weights | Accuracy tracking per expert, Bayesian-style weighting |
| **Volume** | Not used | OBV trend, volume ratio, volume confirmation signals |
| **API keys** | Stored but unused | Polygon, Finnhub, Alpha Vantage, News API, FRED all wired in |

## Quick Start

### One-click (recommended)

1. Edit `user_config.txt` — add your tickers and API keys
2. Double-click `Open_FinSense.bat`

The bat file will:
- Create `.venv` if missing
- Install all dependencies
- Open `http://127.0.0.1:8090/` in your browser
- Start the server

### Manual

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn finsense.api.main:app --reload --port 8090
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Web dashboard |
| GET | `/health` | Health check |
| POST | `/analyze` | Analyze single ticker |
| GET | `/analyze/watchlist` | Analyze all tickers from user_config.txt |
| GET | `/dashboard/watchlist` | Dashboard-formatted watchlist |
| GET | `/dashboard/ticker/{ticker}` | Full ticker detail with chart |
| GET | `/dashboard/forward-horizons/{ticker}` | Multi-horizon projections |
| POST | `/validate/backtest` | Backtest validation |

## Architecture

```
finsense/
  data/
    providers.py      # Yahoo enhanced + mock + resilient composite
    sentiment.py      # Weighted phrase NLP + News API
    macro.py          # FRED API macro regime detection
  experts/
    quant.py          # 12-factor model with full technical overlay
    fundamental.py    # Multi-metric valuation + profitability + moat
    ml.py             # Real GradientBoosting with walk-forward training
  engine/
    consensus.py      # Regime-aware + accuracy-tracked expert fusion
  risk/
    manager.py        # Cornish-Fisher VaR/CVaR, liquidity, drawdown
  api/
    main.py           # FastAPI with all endpoints
  web/
    index.html        # Tailwind CSS dashboard
    app.js            # Interactive SPA
  pipeline.py         # Orchestrator
  models.py           # Data contracts
  config.py           # App settings
```

## API Keys (optional but recommended)

Add to `user_config.txt`:
- `news_api_key` — from newsapi.org (better sentiment)
- `fred_api_key` — from fred.stlouisfed.org (macro regime)
- `polygon_api_key` — from polygon.io (future: options flow)

## Disclaimer

For research and educational purposes only. Not financial advice.
