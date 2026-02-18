# Lessons Learned

## Bugs Found & Fixed

### 2026-02-10: Python .pyc cache can mask code edits
- **Symptom:** Edited `dynamic_sizer.py` to fix `.correlations` → `.get_correlation()`, but system still crashed with the old error message after restart
- **Root cause:** Python was running cached `.pyc` bytecode, ignoring the new `.py` file
- **Fix:** `find /home/pap/Desktop/QuantMuse/data_service -name "*.pyc" -delete && find ... -name "__pycache__" -exec rm -rf {} +`
- **Rule:** After fixing a persistent crash that "shouldn't happen", clear `__pycache__` before restarting

### 2026-02-10: CorrelationState has no `.correlations` attribute
- **Wrong:** `for pair, corr in correlation_state.correlations.items()`
- **Right:** Use `correlation_state.get_correlation(symbol_a, symbol_b)` method
- **Where:** `data_service/risk/dynamic_sizer.py` `get_correlation_multiplier()`
- **CorrelationState** API: `get_correlation(a, b) -> float`, not a dict

### 2026-02-10: Risk check blocks closing trades
- **Symptom:** AMD position at 31% > 30% limit. System tried to close but `pre_trade_check` rejected the close order too. All trades blocked.
- **Fix:** Added `is_closing: bool = False` to:
  - `RiskManager.pre_trade_check()` - bypass size checks for closes
  - `PositionSizer.apply_constraints()` - pass through
  - `OrderManager.create_order()` - pass through
  - `run_multi_strategy.py` - detect close (current_size > 0 and delta < 0, or vice versa)
- **Critical:** `order_manager.create_order()` had its OWN risk check - easy to forget to thread `is_closing` through ALL layers

### 2026-02-10: UnboundLocalError - variable used before fetch
- **Symptom:** `UnboundLocalError: local variable 'current_positions' referenced before assignment`
- **Root cause:** `dynamic_sizer.update_positions(current_positions)` was called at step 4, but `current_positions` was only fetched at step 5
- **Fix:** Moved the positions fetch block to before the dynamic sizer call

### 2026-02-10: Forex Factory returns 403
- **Root cause:** Cloudflare blocking plain requests
- **Fix:** Use `cloudscraper` library (already installed) + PacketStream residential proxies
- **Proxies:** `Sticky_proxies_us.md` - format `user:pass:host:port`

### 2026-02-10: Forex Factory HTML parsing - wrong CSS classes
- **Wrong:** `calendar__cell--date`, `calendar__cell--impact`
- **Right:** `calendar__date`, `calendar__time`, `calendar__currency`, `calendar__event`, `calendar__impact`
- **Impact icons:** `icon--ff-impact-red` = HIGH, `icon--ff-impact-ora` = MEDIUM, `icon--ff-impact-yel` = LOW
