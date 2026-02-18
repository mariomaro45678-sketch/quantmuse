# 📖 Operations Runbook - Hyperliquid Trading System

This document provides procedures for maintaining and supporting the production trading system.

---

## 🛑 Emergency Procedures

### 1. Immediate System Shutdown
If the system is behaving erratically or a risk limit is breached without automatic closure:
```bash
# Docker setup
docker-compose stop trading-engine

# Local setup
pkill -f main.py
```

### 2. Manual Emergency Position Closure
In case of critical market failure or API issues:
1. Log in to [Hyperliquid DEX UI](https://app.hyperliquid.xyz).
2. Use the "Account" -> "Close All" button.
3. Verify in the Dashboard or CLI that `open_positions` is `[]`.

### 3. Circuit Breaker Recovery
If the `RiskManager` triggers a Circuit Breaker (halts trading):
1. Investigate `logs/app.log` for the "🚨 CIRCUIT BREAKER" entry.
2. Address the underlying cause (extreme drawdown, API disconnect, etc.).
3. Reset the system by restarting the process.

---

## 🛠 Maintenance Tasks

### 1. Database Backups
The system uses SQLite for high-speed local persistence.
```bash
# Create a snapshot backup
cp hyperliquid.db backups/hyperliquid_$(date +%Y%m%d_%H%M).db
```

### 2. Log Rotation & Inspection
Logs are automatically rotated by `logging_config.py` (10MB x 5 backups).
- **View last 100 errors**: `tail -n 500 logs/app.log | grep ERROR`
- **Follow logs**: `tail -f logs/app.log`

### 3. API Key Rotation
To rotate keys without major downtime:
1. Update `.env` with new keys.
2. Restart the process: `docker-compose restart trading-engine`.
3. Verify connection in logs: "Trading system initialized in ... mode".

---

## 🧩 Troubleshooting

| Symptom | Cause | Resolution |
| :--- | :--- | :--- |
| `KeyError: 'XAU'` | Market data missing | Verify Hyperliquid Fetcher is receiving ticks. Check internet. |
| `Websocket closed` | Token expired / Net drop | `TradingEngine` will attempt auto-reconnect every 60s. |
| `Insufficient Margin` | Leverage too high | Check `risk_config.json` -> `max_leverage`. Lower it. |
| Dashboard shows 404 | Static files missing | Ensure `dashboard_app.py` is served from the root. |

---

## 📈 Service Recovery

1. **Redis Failure**: If Redis crashes, restart it: `docker-compose restart redis`. The dashboard will reconnect automatically.
2. **Database Corruption**: If `hyperliquid.db` is corrupt, rename it and run `python migrations/init_db.py` to start fresh.
3. **Ghost Positions**: If the exchange shows positions but the system doesn't, Run `python scripts/sync_positions.py` (Drafted in Phase 9).

---

## 📞 Escalation
- **System Owner**: [User]
- **API Support**: Hyperliquid Discord / API Docs
- **Infrastructure**: Local Host / Docker Runtime
