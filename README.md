# Trading Bridge: TradingView → Alpaca

Receives TradingView webhook alerts and executes trades on Alpaca Paper Trading.

## Architecture

```
TradingView Indicator → TradingView Alert → HTTPS Webhook
    → FastAPI Server → Alpaca Paper Trading API → Order Execution
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your Alpaca keys and webhook secret

# 3. Run tests
pytest tests/ -v

# 4. Start server
python app.py
```

## TradingView Alert Setup

### Alert Message (JSON)

**For BUY:**
```json
{
  "secret": "MY_WEBHOOK_SECRET",
  "strategy": "Faisal_SPY_V1",
  "symbol": "{{ticker}}",
  "action": "BUY",
  "price": "{{close}}",
  "time": "{{timenow}}"
}
```

**For SELL:**
```json
{
  "secret": "MY_WEBHOOK_SECRET",
  "strategy": "Faisal_SPY_V1",
  "symbol": "{{ticker}}",
  "action": "SELL",
  "price": "{{close}}",
  "time": "{{timenow}}"
}
```

### Webhook URL
```
https://YOUR_DOMAIN/webhook/tradingview
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | System status + account info |
| POST | `/webhook/tradingview` | Receive TradingView alerts |
| GET | `/signals` | Recent signals log |
| GET | `/trades` | Recent trades log |

## Manual Test

```bash
# BUY signal
curl -X POST https://YOUR_DOMAIN/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{"secret":"MY_WEBHOOK_SECRET","strategy":"Faisal_SPY_V1","symbol":"SPY","action":"BUY","price":"550.25","time":"2026-09-09T16:30:00Z"}'

# SELL signal
curl -X POST https://YOUR_DOMAIN/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{"secret":"MY_WEBHOOK_SECRET","strategy":"Faisal_SPY_V1","symbol":"SPY","action":"SELL","price":"555.00","time":"2026-09-09T17:00:00Z"}'
```

## Safety Features

- **Paper trading ONLY** — blocks live API endpoints
- **Webhook secret validation** — rejects unauthorized requests
- **Symbol whitelist** — only permitted symbols accepted
- **Duplicate protection** — SQLite + deterministic signal_id
- **No auto-reversal** — SELL closes long, never opens short
- **Fast webhook response** — execution runs in background
- **Persistent state** — survives server restarts (SQLite)

## Cloud Deployment (Railway)

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login & deploy
railway login
railway init
railway up

# Set environment variables in Railway dashboard
```

Or use the Dockerfile with any container host (Render, Fly.io, VPS).

## Files

```
├── app.py              # FastAPI server (main)
├── broker.py           # Alpaca trading interface
├── config.py           # Configuration from env vars
├── store.py            # SQLite persistent storage
├── tests/
│   └── test_webhook.py # All 12+ test cases
├── requirements.txt
├── Dockerfile
├── .env.example
├── .gitignore
└── README.md
```
