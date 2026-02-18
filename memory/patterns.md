# Useful Patterns & Commands

## Debugging

### Clear Python bytecode cache
```bash
find /home/pap/Desktop/QuantMuse/data_service -name "*.pyc" -delete
find /home/pap/Desktop/QuantMuse/data_service -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
```
Use when: Code edits don't seem to take effect, or error message references old code.

### Check if system is running
```bash
ps aux | grep run_multi | grep -v grep
```

### Watch live log
```bash
ls -t /home/pap/Desktop/QuantMuse/logs/prod_*.log | head -1 | xargs tail -f
```

### Check for specific errors in latest log
```bash
ls -t /home/pap/Desktop/QuantMuse/logs/prod_*.log | head -1 | xargs grep -E "ERROR|REJECTED|AttributeError" | tail -20
```

### Restart cleanly
```bash
pkill -f run_multi_strategy.py 2>/dev/null; sleep 2
# Then clear cache if needed, then:
nohup venv/bin/python3 scripts/run_multi_strategy.py --duration 24 > logs/prod_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```

## Architecture Patterns

### Threading a flag through the risk stack
When adding a new bypass flag (e.g. `is_closing`), it must be threaded through ALL layers:
1. `RiskManager.pre_trade_check(is_closing=)` - the gate
2. `PositionSizer.apply_constraints(is_closing=)` - passes through
3. `OrderManager.create_order(is_closing=)` - passes through
4. `run_multi_strategy.py` - compute the flag and pass to both sizer and order_mgr

### Detecting closing vs opening trades in run_multi_strategy.py
```python
is_closing = False
if current_size > 0 and delta_size < 0:  # Long position, reducing/closing
    is_closing = True
elif current_size < 0 and delta_size > 0:  # Short position, covering
    is_closing = True
```

## Free Data Sources

### Forex Factory scraping
- Use `cloudscraper` (not `requests`) to bypass Cloudflare
- Use PacketStream proxies from `Sticky_proxies_us.md`
- Cache for 6h to avoid rate limits
- CSS classes: `calendar__date/time/currency/event/impact`
- Impact icon classes: `icon--ff-impact-red/ora/yel`
