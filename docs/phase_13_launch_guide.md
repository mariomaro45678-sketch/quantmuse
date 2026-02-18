# Phase 13: Paper Trading Launch Guide

## 🚀 Quick Start (Day 1)

### Prerequisites Checklist
- [x] Testnet wallet funded with 5,000 USDC
- [ ] Testnet private key added to `.env.testnet`
- [ ] Both config files reviewed
- [ ] Screen utility installed (`sudo apt install screen` if needed)

---

## Step-by-Step Launch

### 1. Add Your Testnet Private Key

**IMPORTANT**: Edit `.env.testnet` and add your actual testnet private key:

```bash
nano .env.testnet
```

Find this line:
```
# HYPERLIQUID_PRIVATE_KEY=your_testnet_private_key_here
```

Replace with:
```
HYPERLIQUID_PRIVATE_KEY=0x...your...actual...key...
```

**Save**: Ctrl+O, Enter, Ctrl+X

> ⚠️ **Security**: Never commit this file to git. It's in `.gitignore`.

---

### 2. Launch Testnet Crypto Track (BTC, ETH)

```bash
# Start screen session
screen -S testnet_crypto

# Activate venv
source venv/bin/activate

# Set environment
export $(cat .env.testnet | xargs)

# Launch trading engine
PYTHONPATH=. python main.py \
  --mode testnet \
  --strategy momentum_perpetuals \
  --symbols BTC,ETH \
  --log-level INFO

# Detach from screen: Ctrl+A then D
```

**Expected**: You should see:
```
🤖 Trading Engine started in testnet mode
📊 Strategy: momentum_perpetuals
🎯 Assets: BTC, ETH
```

---

### 3. Launch Mock-Live Metals Track (XAG, XAU)

```bash
# Start separate screen session
screen -S mock_metals

# Activate venv
source venv/bin/activate

# Set environment
export $(cat .env.mock_live | xargs)

# Launch trading engine
PYTHONPATH=. python main.py \
  --mode mock \
  --strategy mean_reversion_metals \
  --symbols XAG,XAU \
  --log-level INFO

# Detach from screen: Ctrl+A then D
```

**Expected**: You should see:
```
🤖 Trading Engine started in mock mode
📊 Strategy: mean_reversion_metals
🎯 Assets: XAG, XAU
```

---

### 4. Launch Dashboard (Optional)

```bash
# Start dashboard screen
screen -S dashboard

# Activate venv
source venv/bin/activate

# Launch dashboard
PYTHONPATH=. python backend/dashboard_app.py

# Detach: Ctrl+A then D
```

**Access**: Open browser to http://localhost:8000

---

### 5. Verify All Systems Running

```bash
# Quick health check
python scripts/health_check.py
```

**Expected**:
```
✅ Testnet Crypto Process
✅ Mock Metals Process
✅ Dashboard Process
✅ Database Accessible
🎉 All systems operational!
```

---

## Daily Monitoring Commands

### Morning Check (Every Day ~9 AM)

```bash
# Health check
python scripts/health_check.py

# Daily report
python scripts/monitor_paper_trading.py

# Check for errors
grep ERROR logs/app.log | tail -20
```

### Evening Check (Every Day ~6 PM)

```bash
# Generate daily report
python scripts/monitor_paper_trading.py

# View recent trades
sqlite3 hyperliquid.db "SELECT symbol, side, pnl, entry_time FROM trades ORDER BY entry_time DESC LIMIT 10;"
```

---

## Useful Screen Commands

```bash
# List all screen sessions
screen -ls

# Reattach to testnet
screen -r testnet_crypto

# Reattach to mock
screen -r mock_metals

# Reattach to dashboard
screen -r dashboard

# Detach from current screen
Ctrl+A then D

# Kill a screen session (if needed)
screen -X -S testnet_crypto quit
```

---

## Viewing Logs

```bash
# Testnet crypto logs
tail -f logs/app.log | grep "testnet"

# Mock metals logs
tail -f logs/app.log | grep "mock"

# All errors
tail -f logs/app.log | grep "ERROR"

# Last 100 lines
tail -100 logs/app.log
```

---

## Troubleshooting

### If process crashes:

1. Check logs:
```bash
tail -200 logs/app.log | grep -A 10 "CRITICAL\|Traceback"
```

2. Reattach to screen:
```bash
screen -r testnet_crypto  # or mock_metals
```

3. Restart if needed:
```bash
# Press Ctrl+C to stop
# Then re-run the launch command
```

### If no trades for 24+ hours:

1. Check if signals are being generated:
```bash
sqlite3 hyperliquid.db "SELECT COUNT(*) FROM trades WHERE entry_time > datetime('now', '-48 hours');"
```

2. Verify WebSocket connection in logs:
```bash
grep "WebSocket" logs/app.log | tail -10
```

3. Check market conditions (might be ranging/consolidating)

---

## Emergency Stop

If you need to stop everything immediately:

```bash
# Kill all screens
screen -X -S testnet_crypto quit
screen -X -S mock_metals quit
screen -X -S dashboard quit

# Or killall
killall -9 python
```

Then manually close any positions on testnet UI if needed.

---

## Phase 13 End Goals (Day 14)

After 14 days, you should have:

- [ ] 10+ days of continuous uptime
- [ ] 20+ trades executed (combined)
- [ ] Win rate within ±15% of backtest
- [ ] Zero critical system errors
- [ ] Daily journal with observations
- [ ] Confidence to deploy $100 live

**Next Phase**: Live trading with $100 real capital

---

## Questions?

Check logs first, then review:
- Implementation plan: `artifacts/implementation_plan.md`
- Walkthrough: `artifacts/walkthrough.md`
- Master task: `_MASTER_TASK.md` Phase 13
