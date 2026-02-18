# Phase 13: Paper Trading - LAUNCHED! 🚀

**Launch Date**: 2026-02-03  
**Status**: ✅ ACTIVE  

## Running Processes

### Testnet Crypto Track
- **PID**: 417212
- **Assets**: BTC, ETH
- **Strategy**: Momentum Perpetuals
- **Mode**: Real execution on testnet
- **Capital**: 5,000 USDC
- **Log**: `logs/testnet.log`

### Mock-Live Metals Track
- **PID**: 417220
- **Assets**: XAG, XAU
- **Strategy**: Mean Reversion Metals
- **Mode**: Real data, simulated execution
- **Capital**: 5,000 USDC (virtual)
- **Log**: `logs/mock.log`

---

## Daily Monitoring Commands

### Quick Status
```bash
# View running processes
ps aux | grep "main.py"

# Daily performance report
python scripts/monitor_paper_trading.py

# Check logs
tail -f logs/testnet.log  # Testnet crypto
tail -f logs/mock.log     # Mock metals
```

### Kill Processes (If Needed)
```bash
# Stop testnet
kill 417212

# Stop mock
kill 417220

# Or kill all
pkill -f "main.py"
```

---

## Expected Behavior

Over the next 14 days, you should observe:
- Trades executing on both tracks
- Position entries/exits based on signals
- P&L accumulating in database
- Risk snapshots being recorded every 10s

**First Trade**: Typically within 1-6 hours depending on market conditions

---

## Troubleshooting

If processes crash:
1. Check logs: `tail -100 logs/testnet.log`
2. Look for errors: `grep ERROR logs/testnet.log`
3. Relaunch manually using commands in `docs/phase_13_launch_guide.md`

---

## Next Check-In

Run this tomorrow morning (~9 AM):
```bash
python scripts/monitor_paper_trading.py
```

This will show you:
- How many trades executed
- Win rate
- P&L
- System health

Good luck! 🎯
