# MaguraBot — Solana Memecoin Sniper Bot

Ultra-responsive, multi-agent Solana memecoin sniper bot and live web dashboard. Built for **Pump.fun** bonding curves and **Raydium** liquidity pools with automated multi-factor anti-rug filters, automated Take Profit (TP), Stop Loss (SL), Trailing Stops, and dual-mode **Paper Trading** & **Live Execution**.

---

## Features

- **Multi-Source Discovery Agent**:
  - Live **Pump.fun** mints and bonding curve tracking via PumpPortal WebSocket (`wss://pumpportal.fun/api/data`).
  - Live **Raydium** AMM and CPMM new liquidity pool listener.
- **Sub-Second Multi-Factor Risk Agent**:
  - **Mint Authority Check**: Enforces revoked mint authority (prevents infinite supply mints).
  - **Freeze Authority Check**: Enforces revoked freeze authority (prevents honeypots).
  - **RugCheck Integration**: Rapid rating assessment and risk factor tagging.
  - **Bonding Curve Progress Gate**: Configurable max bonding curve percentage.
- **Dual Execution Engine**:
  - **Paper Trading (Simulation)**: Real-time price movement, realistic slippage (1.5%), zero capital risk, virtual SOL tracking.
  - **Live Trading**: Direct on-chain swaps via **Jupiter Swap API v6** and native Solana transactions using `solders` and optional Jito tip bundling.
- **Automated Exit Strategies**:
  - Configurable **Take Profit** (e.g. `+50%`, `+100%`).
  - Configurable **Stop Loss** (e.g. `-20%`).
  - Dynamic **Trailing Stop Loss** (locks in gains after a token peaks).
  - Max hold time safety timeout.
- **Real-Time Web Terminal & Dashboard**:
  - High-performance dark terminal styled with Magura design language.
  - Live WebSocket radar streaming incoming tokens and risk ratings.
  - Active positions table with live ticking PnL and one-click market sell.
  - Emergency **"Sell All"** circuit breaker.
  - Dynamic configuration adjustments without restarting the engine.

---

## Architecture

```text
MaguraBot/
├── backend/
│   ├── agents/
│   │   ├── discovery_agent.py   # Streams tokens from Pump.fun and Raydium
│   │   ├── risk_agent.py        # Multi-factor anti-rug screening (<20ms)
│   │   └── trade_agent.py       # Position sizing, concurrency, and cooldowns
│   ├── engine/
│   │   ├── sniper.py            # Central coordinator & WebSocket broadcaster
│   │   ├── executor.py          # Dual-mode execution (Paper simulation & Live on-chain)
│   │   └── position_tracker.py  # Real-time position monitor for TP, SL, Trailing SL
│   ├── models/
│   │   ├── config.py            # BotConfig settings
│   │   ├── token.py             # TokenData and TokenSafetyScore
│   │   ├── order.py             # Order and Position models
│   │   └── events.py            # WebSocket event models
│   ├── services/
│   │   ├── solana_client.py     # RPC connection, balances, keypair signing
│   │   ├── pumpportal.py        # Pump.fun WebSocket feed client
│   │   ├── raydium.py           # Raydium new pool listener
│   │   ├── jupiter.py           # Jupiter v6 Swap API aggregator
│   │   └── security.py          # On-chain authority verification & RugCheck
│   └── main.py                  # FastAPI REST API, WebSocket server, and static UI
├── dashboard/
│   └── index.html               # Real-time Tailwind CSS sniper dashboard
├── scripts/
│   └── run_bot.sh               # Easy startup script
└── tests/
    ├── test_sniper.py           # Core unit tests
    └── test_api.py              # API endpoint tests
```

---

## Quick Start

### 1. Prerequisites
- Python 3.10+
- `uv` (recommended) or standard `python3 -m venv`

### 2. Setup & Installation
```bash
git clone https://github.com/reynaxk/MaguraBot.git
cd MaguraBot

# Create environment and install dependencies
uv venv .venv
uv pip install --python .venv -r requirements.txt
```

### 3. Configuration
Copy the sample environment file:
```bash
cp .env.example .env
```

Configure your parameters:
```env
BOT_MODE=paper              # 'paper' or 'live'
DEFAULT_BUY_AMOUNT_SOL=0.05
TAKE_PROFIT_PCT=50.0
STOP_LOSS_PCT=20.0
TRAILING_STOP_PCT=15.0
MAX_POSITIONS=5
```

### 4. Run the Sniper & Dashboard
```bash
./scripts/run_bot.sh
```
Open your browser at **`http://localhost:8000`** to access the live dashboard.

---

## Testing

Run the automated test suite:
```bash
.venv/bin/python -m unittest discover tests
```
