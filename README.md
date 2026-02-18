# 🚀 Hyperliquid DEX Trading System

An automated, high-frequency trading system for metals and crypto on the Hyperliquid DEX. Featuring real-time news sentiment analysis, multi-timeframe quantitative strategies, and robust risk management.

---

## 🛠 Features

-   **Multi-Asset Support**: High-performance trading for Gold (XAU), Silver (XAG), Copper (HG), and Crypto (BTC, ETH).
-   **News & Sentiment Analysis**: NLP-powered sentiment scoring from multi-source aggregators.
-   **Quantitative Strategies**: Adaptive momentum and mean-reversion with cross-timeframe validation.
-   **Production Hardening**:
    -   Centralized `TradingEngine` with automatic recovery.
    -   Unified SQL storage (`hyperliquid.db`) with composite indexing.
    -   Log-level secret redaction (Wallet/API keys).
    -   Circuit breakers and real-time risk snapshots (VaR/CVaR).
-   **Web Dashboard**: Glassmorphism UI for real-time P&L, position tracking, and alerts.

---

## 📦 Quick Start

### 1. Local Development (Mock Mode)
No API keys required. Perfect for testing strategy logic.
```bash
# Clone and setup environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure secrets
cp .env.example .env

# Run Trading Engine (Mock)
python main.py --mode mock

# Run Dashboard
python backend/dashboard_app.py
```
Dashboard available at `http://localhost:8000`

### 2. Docker Deployment (Production-Ready)
Containerized setup with separate services for trading, dashboard, and redis.
```bash
# Start all services
docker-compose up -d --build

# View logs
docker-compose logs -f trading-engine
```
Dashboard available at `http://localhost:8001` (default map)

---

## 🚦 Operational Commands

| Command | Description |
| :--- | :--- |
| `python main.py --mode live` | Start mainnet trading (Requires VALID_WALLET and API_KEY in .env) |
| `python main.py --mode paper-trading` | Start testnet trading |
| `python scripts/e2e_mock_run_p9.py` | Run E2E logic validation |
| `pytest tests/` | Execute full test suite |
| `python migrations/init_db.py` | Reset/Initialize production database |

---

## 📁 System Architecture

```text
├── main.py                  # Entry Point (TradingEngine Orchestrator)
├── backend/                 # FastAPI Dashboard & WebSocket
├── data_service/            # Core Business Logic
│   ├── ai/                 # NewsProcessor & Sentiment Models
│   ├── executors/          # OrderManager & Hyperliquid SDK Bridge
│   ├── risk/               # RiskManager (VaR/CVaR) & PositionSizer
│   ├── strategies/         # MomentumPerpetuals, etc.
│   └── storage/            # DatabaseManager (Unified SQLite)
├── Dockerfile               # Production container definition
└── docker-compose.yml       # Multi-service orchestration
```

---

## 🔒 Security & Best Practices

1.  **Secrets**: NEVER commit your actual `.env` file. The logging system automatically redacts wallet addresses and API keys, but physical security of the `.env` file remains your responsibility.
2.  **Mock First**: Always run new strategies in `--mode mock` for at least 24 hours to observe signal quality and risk behavior.
3.  **Docker Ports**: Port 6380 (Redis) and 8001 (Dashboard) are used by default to avoid conflicts with existing host services. These can be adjusted in `.env`.

---

## 📊 Monitoring

-   **Dashboard**: Access `trade.local:8001` or `localhost:8001` for real-time pulse.
-   **Logs**: Check `logs/app.log` for detailed execution traces. Use the `/api/logs` endpoint for remote inspection.
-   **Alerts**: Critical risk alerts (Circuit breakers) are broadcasted immediately via WebSockets and persisted in the `alerts` table.

---

## 📜 License
Proprietary - Research & Trading Software. Use at own risk.
