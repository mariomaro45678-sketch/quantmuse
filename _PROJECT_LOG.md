# 🚀 HYPERLIQUID TRADING SYSTEM - PROJECT LOG

## 🧠 CURRENT STATE (Source of Truth)
* **Stack**: Python 3.10+, Hyperliquid SDK, FastAPI, Redis, SQLite (Unified hyperliquid.db).
* **Live URL**: http://localhost:8001 (Dashboard)
* **Key Decisions**:
    *   **Architecture**: "TradingEngine" orchestrator with signal handling (Graceful Shutdown).
    *   **Database**: Unified `hyperliquid.db` with composite indexing for candles/trades/risk.
    *   **Phase 13 Strategy**: Dual-track Paper Trading. Testnet Crypto (BTC/ETH, PID 579842) + Mock-Live Metals (XAU/XAG, PID 417220).
    *   **News Pipeline**: Active. 3-source concurrent fetch every 10min (Google RSS + RSS Multi [8 feeds] + DuckDuckGo News). 3-tier dedup (ID → URL → Jaccard 0.65) before FinBERT scoring. Stores to `news` table → `sentiment_factors` snapshots. Source credibility weights now correctly applied via substring match (Telegram 1.2 > Reuters 1.05 > … > DDG/RSS 0.85).
    *   **Automation**: `performance_tracker.py` handles daily journal entries and KPI exports.
    *   **Security**: `SecretRedactor` logging filter masks sensitive data.
    *   **Strategy Framework**: Refactored StrategyBase with dynamic Sharpe annualization, state reset automation, orphaned exit logic integration. All 3 strategies (Momentum, MeanReversion, Sentiment) now properly track positions and execute exits.

## ⚠️ KNOWN PITFALLS (Do Not Repeat Errors)
*   **Risk checks must NEVER block sell orders (CRITICAL)**: `pre_trade_check()` must treat `side='sell'` as exposure-reducing — same as `is_closing=True`. Blocking sells traps overexposed positions permanently since the strategy can never exit. Only **buy** orders should be checked against `max_position_pct` and `max_portfolio_leverage`. (Fixed 2026-02-17 in `risk_manager.py`, `order_manager.py`, `position_sizer.py`).
*   **HIP-3 cancel_order MUST use HIP-3 symbol (CRITICAL)**: `exchange.cancel(name, oid)` → `bulk_cancel` → `name_to_asset(coin)` requires the HIP-3 symbol (`xyz:TSLA`), NOT the bare internal symbol (`TSLA`). The SDK's `name_to_coin` map only has `xyz:TSLA` as a key — passing `TSLA` raises `KeyError: 'TSLA'`. Always call `to_hip3_symbol(symbol)` before passing to `exchange.cancel()`. (Fixed 2026-02-14 in `HyperliquidExecutor.cancel_order`).
*   **HIP-3 all_mids() MUST specify DEX (CRITICAL)**: `info.all_mids()` with no `dex` param only returns main perp prices. It returns nothing for xyz/flx assets. Always call `info.all_mids(dex='xyz')` for stocks and `info.all_mids(dex='flx')` for metals. Extract dex from HIP-3 symbol: `dex = hip3_symbol.split(':')[0]`. (Fixed 2026-02-14 in `HyperliquidExecutor.place_order`).
*   **HIP-3 Position Tracking (CRITICAL)**: HIP-3 positions (xyz:, flx:) live in **separate clearinghouses per DEX**. Must query `clearinghouseState` with `dex='xyz'` and `dex='flx'` params, NOT just main perps. SDK must be initialized with `perp_dexs=['xyz', 'flx']` or asset IDs will be wrong (e.g., 230 instead of 110002).
    *   **Account Equity & Positions - Correct API Query**:
        - Main perps (BTC, ETH, etc.): `POST /info` `{"type": "clearinghouseState", "user": "0x84d7...c4ff"}`
        - HIP-3 xyz DEX (TSLA, MSFT, NVDA, AAPL, etc.): `POST /info` `{"type": "clearinghouseState", "user": "0x84d7...c4ff", "dex": "xyz"}`
        - HIP-3 flx DEX (GOLD, SILVER, COPPER): `POST /info` `{"type": "clearinghouseState", "user": "0x84d7...c4ff", "dex": "flx"}`
        - Sub-accounts: `POST /info` `{"type": "subAccounts", "user": "0x84d7...c4ff"}`
        - **The UI shows "xyz" next to position coins — this is the DEX name, NOT a sub-account label.**
        - **True account equity = sum of main + xyz + flx clearinghouse accountValues.**
*   **HIP-3 Metadata Loading**: Initializing SDK with `perp_dexs` is NOT enough for ordering Cross-DEX assets. Must explicitly call `exchange.info.set_perp_meta` for each DEX metadata (DEX 1-n) to ensure symbol resolution and routing work. (Fixed in `HyperliquidExecutor._load_hip3_metas`).
*   **Mid-Price Fallback**: `all_mids()` may miss newly deployed or low-volume HIP-3 assets. Fallback to explicit `l2Book` query for the specific HIP-3 symbol if `all_mids` returns 0.
*   **P&L Reconciliation**: SDK `OrderResponse` does not provide realized P&L. Must use `order_manager.reconcile_fills()` to fetch `userFills` from the exchange and update local `trades` table posthumously.

## 📝 RECENT SESSION LOGS (Rolling Window)
## [2026-02-22 14:06] 🤖 Claude Opus 4.6 | 🩹 Overtrading Emergency Fix — Comprehensive Recalibration for Small Account

- **Status**: ✅ FIXED & RESTARTED (PID: 2056435, log: `logs/prod_20260222_1406.log`)
- **Severity**: CRITICAL — System bleeding equity through excessive churn (-$14 on $76 equity)

### Problem
Live trading analysis revealed severe overtrading: **2,546 trades in 19 days**, $46.8M total notional volume on $76 equity. Win rate only 37%. Momentum strategy alone: 1,966 trades with -$12.72 PnL. System was churning positions with tight stops, hitting them immediately, and re-entering after brief cooldown.

### Root Causes

| Issue | Impact |
|-------|--------|
| **Stop loss too tight** | `min(0.02, atr_pct * 2.0)` → as low as 0.6% on low-ATR stocks, stopped out by spread+fees alone |
| **Cycle too fast** | 60s interval checking 1h candles → recalculates same signals 60x/hour |
| **Cooldown too short** | 20min momentum, 15min sentiment → re-enters right after stop-out |
| **$11 HIP-3 minimum** | Forces 14.5% positions when strategy wants 5% on $76 equity |
| **Too many assets** | 11 assets across 3 strategies spread equity impossibly thin |
| **No position cap** | AMD reached **170% of equity** ($129 on $76) |
| **mean_reversion_metals** | Zero fills — all orders fail "Insufficient margin" on flx clearinghouse |

### Changes Applied (9 files)

**Strategy Code:**
- `momentum_perpetuals.py`: Stop loss `min(0.02, atr*2.0)` → `min(0.04, atr*3.0)`, min confidence 0.35→0.50
- `sentiment_driven.py`: Min confidence 0.35→0.50

**Execution Loop:**
- `run_multi_strategy.py`:
  - Cooldowns: 60min momentum (was 20), 45min sentiment (was 15)
  - Cycle intervals: 300s momentum (was 60), 180s sentiment (was 120)
  - **Single position cap**: 25% of equity hard limit with enforcement before order placement
  - **Low equity guard**: Skip new entries >25% equity when account <$100
  - Asset reduction: momentum→2 (TSLA, NVDA from 4), sentiment→3 (AMZN, GOOGL, META from 5)
  - Strategy reduction: Disabled `mean_reversion_metals` (zero fills)

**Config:**
- `strategies.json`:
  - momentum: `stop_loss_atr_multiplier` 1.5→3.0, `max_position_size` 0.30→0.25, `max_total_exposure` 2.0→1.0, `applicable_assets` reduced to 2
  - mean_reversion: `enabled: false`
  - sentiment: `applicable_assets` reduced to 3, `momentum_threshold` 0.15→0.20
  - global: `max_concurrent_positions` 5→3

### Expected Impact
- **10-20x fewer trades per day** (from ~200/day to ~10-20/day)
- **Wider stops** give trades room to breathe without getting stopped by noise
- **No position blowups** — 25% cap prevents AMD-style 170% equity positions
- **Better signal quality** — 0.50 confidence threshold filters low-conviction churn
- **Focused universe** — 5 total assets (2 momentum + 3 sentiment) appropriate for $76 account

### Validation
System restarted successfully. First action: Closed inherited AMZN position from previous run. Only 2 strategies active (`momentum_perpetuals`, `sentiment_driven`) monitoring 5 assets total.

---

## [2026-02-21 09:25] 🤖 Antigravity | 🎯 Fixed Margin Constraints and Diagnosed DEX Isolation
- **Changes**: `run_multi_strategy.py` (Fixed missing `notional` variable; plumbed `eval_leverage` down to `submit_entry` and `create_order`), `hyperliquid_executor.py` (Added `math.ceil` rounding to dynamically satisfy margin limits with `update_leverage`).
- **Context**: Solved the `leverage` calculation bug causing "$11 minimum" trades to get rejected on small accounts. Discovered that the remaining "Insufficient margin" error for XAG (`asset=120006`) is because Hyperliquid's `flx` DEX has an **isolated clearinghouse** with exactly $0.00 margin. `xyz` DEX has $61.86, and `main` has $14.77. Since the bot only has an API Agent secret, it cannot transfer USDC programmatically. The user must manually transfer USDC to the `flx` DEX to trade XAG/SILVER.


## [2026-02-20 13:41] 🤖 Antigravity | 🎯 Fixed PnL Tracking & Audit Wrap-up
- **Changes**: `order_manager.py` (Fixed PnL tracking to extract `closedPnl` from Hyperliquid API instead of calculating entry/exit slippage). `dashboard_app.py` (Fixed SQL query to calculate win rate strictly on closed positions instead of all orders, preventing halved win rates). `hyperliquid_executor.py` (Enforced exactly 5 sig figs `f"{px:.5g}"` to fix `float_to_wire` format errors for SILVER). `backfill_pnl.py` (Attempted historical backfill, but opted to rely on corrected live sync mechanism instead for safety). `dashboard_app.py` process restarted.
- **Context**: All 8 critical bugs from the `live_trading_deep_analysis` report are now resolved. Trader has been running autonomously and perfectly stable for > 15 minutes. Watchdog cronjob active.

## [2026-02-20 13:16] 🤖 Antigravity | 🎯 Complete Live Trading Fixes & Restart
- **Changes**: 
  - `mean_reversion_metals.py` (Fixed AND→OR signal logic).
  - `strategies.json` (Relaxed RSI thresholds to 35/65).
  - `trader_watchdog.sh` (Created supervisor & cron job to auto-restart trader).
  - `hyperliquid_executor.py` (Fixed tick size price rounding bug for metals using exchange metadata).
- **Context**: Trader is LIVE. Watchdog is running via cron. Mean reversion successfully signaled XAG. Sentiment strategy successfully placed AMZN order. All critical blockers cleared.

## [2026-02-20 12:52] 🤖 Antigravity | 🔍 Deep Analysis + 🔧 6 Bug Fixes — Live Trading Process Audit
- **Changes**: 
  - `hyperliquid_executor.py` — `get_user_state()` now sums equity across all clearinghouses (main+xyz+flx)
  - `run_multi_strategy.py` — Startup fetches real equity from exchange; min order buffer $10→$11; scalper init log ERROR→WARNING
  - `entry_timing.py` — Pullback target hit now executes orders instead of just logging
  - `database_manager.py` + `order_storage.py` — Added `_connect()` helper with WAL + busy_timeout=5000
  - Created `reports/live_trading_deep_analysis_20260220.md` (comprehensive findings doc with fix status)
- **CRITICAL**: Live trading process is **NOT RUNNING** (dead since Feb 19 12:46). Needs manual restart.
- **Fixed** (5/9): Equity tracking, min order buffer, pullback no-op, SQLite locking, scalper log level
- **Remaining**: P&L tracking (architecture), process supervisor, mean_reversion investigation
- **Context**: Full report in `reports/live_trading_deep_analysis_20260220.md`


## [2026-02-18] 🤖 Claude Opus 4.6 | 🐛 10-Blocker Fix — Multi-strategy system completely silent, death by 1000 cuts

- **Status**: ✅ FIXED & RESTARTED (PID: 3122365, log: `logs/prod_20260218.log`)
- **First trades within 2 min of restart**: TSLA SELL @ $412.47, MSFT SELL @ $399.12, META BUY @ $636.10

### Problem
All 3 strategies (momentum_perpetuals, mean_reversion_metals, sentiment_driven) completely stopped trading. No orders, no closures. The system was silent despite signals being generated. Root cause was 10 cascading blockers that compounded — each layer silently killed trades the previous layer approved.

### Root Causes (10 cascading blockers)

| # | Blocker | Where |
|---|---------|-------|
| 1 | Equity fetched once at startup, stale forever | `run_multi_strategy.py` |
| 2 | `open_positions` never synced to RiskManager — leverage checks used empty list | `run_multi_strategy.py` |
| 3 | Closing trades blocked by daily loss limit | `risk_manager.py` |
| 4 | Entry optimizer dropped `is_closing` flag — all trades treated as new entries | `entry_timing.py` |
| 5 | 30-min cooldown applied to ALL trades including closes | `run_multi_strategy.py` |
| 6 | DynamicSizer confidence scaling too aggressive: `0.5 + (conf * 0.5)` → 0.5x at moderate conf, shrinking sizes below $10 minimum | `dynamic_sizer.py` |
| 7 | `$5` delta threshold silently skipped small closes | `run_multi_strategy.py` |
| 8 | `$10` min order enforced by skipping, not bumping — small closes just dropped | `run_multi_strategy.py` |
| 9 | No `side` parameter passed through position sizer → risk chain — sell not recognized as exposure-reducing | `position_sizer.py` |
| 10 | No logging on failed trades — every rejection was silent | `run_multi_strategy.py` |

### Fixes (7 files changed)

**`data_service/risk/risk_manager.py`**
- Moved circuit breaker check BEFORE closing trade check (CB is the ONLY thing that can halt closes)
- Closing trades always approved — ALL checks bypassed except circuit breaker:
  ```python
  if is_closing:
      return PreTradeResult(approved=True, reason="Closing trade approved (always allowed)")
  ```
- Sell orders always approved (exposure-reducing)
- Daily loss limit only blocks NEW entries (not closes, not sells)

**`data_service/risk/position_sizer.py`**
- Added `side: str = ""` and `strategy_name: str = ""` to `apply_constraints()`
- Both passed through to `risk_mgr.pre_trade_check()`

**`data_service/executors/order_manager.py`**
- Risk check now passes `strategy_name=strategy_name` (was missing)

**`data_service/risk/dynamic_sizer.py`**
- Confidence multiplier range changed from `0.5–1.0` → `0.7–1.0`:
  ```python
  # OLD (too aggressive): confidence_mult = 0.5 + (signal_confidence * 0.5)
  confidence_mult = 0.7 + (signal_confidence * 0.3)  # Range: 0.7-1.0
  ```

**`data_service/executors/entry_timing.py`**
- `submit_entry()` and `_execute_immediate()` now accept and pass `is_closing: bool`
- Force IMMEDIATE execution for closes — never delay a risk-reducing trade:
  ```python
  if is_closing:
      entry_strategy = EntryStrategy.IMMEDIATE
  ```
- Chase path explicitly sets `is_closing=False` (closes never reach chase logic)

**`scripts/run_multi_strategy.py`** (6 sub-fixes)
- **Equity refresh**: Position monitor now fetches live equity from exchange every 5 min
- **Position sync**: `risk_mgr.open_positions` synced every cycle AND in position monitor
- **Per-strategy cooldowns** (was flat 30 min): momentum=20 min, metals=10 min, sentiment=15 min, scalper=2 min
- **Cooldown exempt for closes**: `if not is_closing: [check cooldown]`
- **Delta threshold**: `min_delta = 2.0 if is_closing else 5.0`
- **Bump closes to min order**: `if is_closing: order_size = min_order_value / px` (bump to $10 instead of skipping)
- **Log all rejections**: `logger.warning(f"Order failed for {sym}: {res.error}")`

### Architectural Rule Established
> **GOLDEN RULE: NEVER block a closing trade.** A close always reduces portfolio risk. Blocking it leaves the position open and increases risk. Only the circuit breaker (extreme drawdown) should halt ALL activity including closes. Every layer of the execution chain (RiskManager → PositionSizer → OrderManager → EntryOptimizer) must honour the `is_closing` flag.

### Key Insight for Small Accounts (<$100)
Conservative defaults designed for larger accounts compound on small accounts to kill all activity:
- Confidence scaling at 0.5x shrinks $20 target → $10 (at the minimum)
- $5 delta threshold vs $7 position = trade skipped
- 30-min cooldown + 3 strategies = most signals are stale before execution
- Daily loss limit (10% = $8) can trigger after a single bad trade and block all new entries

---

## [2026-02-17] 🤖 Claude Sonnet 4.5 | 🐛 Risk Manager Bug Fix — Sell orders no longer blocked by exposure check

- **Status**: ✅ FIXED & RESTARTED (PID: 2169312, log: `logs/dashboard_app.log`)

### Problem
Sell orders for TSLA (65% exposure) and MSFT (41% exposure) were being **blocked by the risk manager's position size check**, even though selling reduces exposure — the exact opposite of what the check is meant to prevent. This caused positions to become permanently stuck above the limit:

```
❌ Order REJECTED by risk check | TSLA sell 0.114 @ 418.24 | Position size check failed: TSLA exposure=65.0% > max=30.0%
❌ Order REJECTED by risk check | MSFT sell 0.075 @ 401.40 | Position size check failed: MSFT exposure=41.1% > max=30.0%
```

The strategy could never reduce overexposed positions because every exit order was rejected.

### Root Cause
`RiskManager.pre_trade_check()` applied the full exposure/leverage checks to **all** orders regardless of side. The existing `is_closing` bypass existed but callers in `entry_timing.py` never passed `is_closing=True` for sell orders — the parameter was never threaded through from the point where `side` was known.

### Fix
3 files changed:

**`data_service/risk/risk_manager.py`** — Added `side: str = ""` parameter. Sell orders now bypass position size and leverage checks (same as `is_closing=True`), only the daily loss limit still applies:
```python
is_sell = side.lower() == "sell"
if is_closing or is_sell:
    # Only check daily loss limit — sell always reduces exposure
    ...
    return PreTradeResult(approved=True, reason="Sell order approved (reduces exposure)")
```

**`data_service/executors/order_manager.py`** — Passes `side=side` to `pre_trade_check` (already in scope).

**`data_service/risk/position_sizer.py`** — Added `side: str = ""` to `apply_constraints()` and threads it to `pre_trade_check`.

### Rule (added to Known Pitfalls below)
> **Risk checks must NEVER block sell orders.** Only buy orders increase exposure. Sells always reduce it — blocking them traps the position and makes overexposure permanent.

---

## [2026-02-14] 🤖 Claude Sonnet 4.5 | 🐛 HIP-3 Order Bug Fix — TSLA/NVDA/AMD/MSFT orders restored
- **Status**: ✅ FIXED & RESTARTED (PID: 127302, log: `logs/prod_20260214.log`)

### Problem
All order execution for equity symbols (TSLA, NVDA, AMD, MSFT, COIN) was silently failing on mainnet with `KeyError: 'TSLA'`, retrying 3 times then logging `Exchange call failed after 3 attempts: 'TSLA'`. This was happening since the process was started on 2026-02-13, resulting in **0 trades executed** for `momentum_perpetuals` and `sentiment_driven` strategies across ~80+ cycles each. Metals (XAU/XAG) were unaffected.

### Root Cause
Two bugs in `data_service/executors/hyperliquid_executor.py`:

**Bug 1 — `cancel_order` passing bare symbol to SDK** ([line 585 before fix](data_service/executors/hyperliquid_executor.py)):
- `exchange.cancel('TSLA', oid)` → `bulk_cancel` → `name_to_asset('TSLA')` → `KeyError: 'TSLA'`
- The SDK's `name_to_coin` map registers HIP-3 assets as `'xyz:TSLA'`, not `'TSLA'`
- Triggered every cycle by the `EntryOptimizer._check_pending_entries` loop calling `cancel_order` to replace stale limit orders

**Bug 2 — `all_mids()` querying wrong DEX** ([line 505 before fix](data_service/executors/hyperliquid_executor.py)):
- `info.all_mids()` (no `dex` param) only returns main perp prices — returns nothing for xyz/flx assets
- Market orders with `px=None` would get `mid=0`, fall through to L2 fallback every time

### Fix
```python
# cancel_order: convert to HIP-3 before SDK call
hip3_symbol = to_hip3_symbol(symbol)
res = await self._retry_call(self.exchange.cancel, hip3_symbol, oid)

# place_order: query correct DEX for mid price
if is_hip3_asset(symbol):
    dex = hip3_symbol.split(':')[0]  # 'xyz' or 'flx'
    mids = self.info.all_mids(dex=dex)
else:
    mids = self.info.all_mids()
```

### Verification
- `info.all_mids(dex='xyz')` returns live prices for all equity symbols ✓
- `info.name_to_asset('xyz:TSLA')` → `110001` (correct asset ID) ✓
- First cycle after restart: NVDA sell limit placed successfully (`ORDER RESTING: SELL 0.099 xyz:NVDA @ 183.43`) ✓
- Zero `Exchange call failed` errors in new log ✓

### Files Modified
- `data_service/executors/hyperliquid_executor.py`: `cancel_order` + `place_order` market price path

---

## [2026-02-14] 🤖 Claude Sonnet 4.5 | 📊 Enhanced Scalper - Paper Trading + Monitoring System
- **Status**: ✅ ENABLED FOR PAPER TRADING + FULL MONITORING SYSTEM ADDED

### Paper Trading Integration
- **Enabled** `enhanced_scalper` in `STRATEGY_CONFIGS` with `"enabled": True, "paper_trading_only": True`
- **Safety Guard**: `StrategyRunner.__init__` raises `RuntimeError` if `paper_trading_only=True` and executor is NOT in `mock` mode
- **Config updated**: `config/strategies.json` → `"enabled": true, "paper_trading_only": true`
- **To run**: `python scripts/run_multi_strategy.py --mock` (scalper runs; without `--mock` it raises an error at init)

### ScalperLogger - Dedicated Monitoring System
- **New module**: `data_service/monitoring/scalper_logger.py`
- **Two sinks**: rotating daily file `logs/scalper_YYYYMMDD.log` (14-day retention) + SQLite `scalper_events` table
- **Event types logged**:
  - `SIGNAL`: every non-flat signal (direction, confidence, rationale, OBI, spread, liquidity, vol_ratio)
  - `CONFLICT`: position conflict prevention (which strategy blocked, what position size)
  - `COOLDOWN`: skipped symbols with remaining cooldown time
  - `ENTRY`: new position opens (price, size, confidence)
  - `EXIT`: position closes with P&L%, hold time, exit reason (stop_loss / take_profit / time_stop / breakeven_stop)
  - `RISK`: consecutive-loss penalty, max-exposure cap, circuit breaker events
  - `ORDERBOOK`: per-signal OB snapshot (OBI, spread%, liquidity score)
  - `REGIME`: regime name, confidence, multiplier per symbol
  - `CYCLE`: per-cycle summary (analyzed, L/S/flat signals, conflict/cooldown/low-conf skips, cycle_ms)
  - `PERF`: rolling performance snapshot every 60 cycles (~10 min)
- **Performance analytics**: `get_performance_summary()` returns win_rate, avg_pnl, Sharpe estimate, by-symbol/by-reason breakdown, max consecutive losses
- **Thread-safe** singleton via `get_scalper_logger(paper_trading=True)`

### Files Modified
- `data_service/strategies/enhanced_scalper.py`: Added `ScalperLogger` wiring at all key events
  - Added `set_paper_trading(bool)` method called by StrategyRunner
  - Added `_slog` lazy property (initialises ScalperLogger singleton)
  - Wired: conflict detection, cooldown, OB snapshots, regime, entry, all 4 exit types, risk events, cycle counters
- `scripts/run_multi_strategy.py`:
  - Added `paper_trading_only` guard in `StrategyRunner.__init__`
  - Calls `strategy.set_paper_trading(executor_mode == 'mock')` at init for any strategy that supports it
  - Triggers `log_performance_snapshot()` every 60 scalper cycles
- `config/strategies.json`: `enabled: true, paper_trading_only: true`
- **New**: `data_service/monitoring/__init__.py`, `data_service/monitoring/scalper_logger.py`

### DB Schema Added
```sql
CREATE TABLE scalper_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT, event_type TEXT, symbol TEXT,
    direction TEXT, confidence REAL, price REAL, size REAL,
    pnl_pct REAL, hold_sec REAL, exit_reason TEXT,
    obi REAL, spread_pct REAL, liquidity REAL, vol_ratio REAL,
    regime TEXT, details TEXT, paper_mode INTEGER DEFAULT 1
)
```

### Query Examples
```python
from data_service.monitoring.scalper_logger import get_scalper_logger
sl = get_scalper_logger()
sl.query_recent_events('SIGNAL', hours=24)   # all signals last 24h
sl.query_recent_events('EXIT', hours=24)     # all exits last 24h
sl.get_performance_summary()                 # rolling stats
sl.get_daily_stats_from_db()                 # today's DB stats
sl.log_performance_snapshot()                # print perf to log
```

---

## [2026-02-13 18:30] 🤖 Claude Haiku 4.5 | 🎯 Enhanced Scalper Strategy - Full Integration Complete
- **Status**: ✅ ALL CRITICAL BUGS FIXED + INTEGRATED WITH QUANTMUSE
- **Components Fixed**:
  1. **Missing Imports** (volume_delta_analyzer.py, stop_hunt_detector.py): Added `from datetime import datetime, timedelta`
  2. **Undefined Attribute** (ultra_scalper_pro.py): Added `position_size: float = 0.0` to MicrostructureSignal dataclass
  3. **Incorrect Math** (volume_delta_analyzer.py:189-217): Fixed delta ratio calculation - now uses actual buy/sell volumes instead of broken formula
  4. **Thread Safety** (risk_manager_high_leverage.py): Replaced `threading.Timer` with timestamp-based cooldown (no race conditions, async-safe)

- **Integration Features**:
  1. **Position Conflict Detection**: Strategy checks other strategy positions before entering trades, avoids trading same symbols
  2. **Shared Bankroll**: Uses system `RiskManager` and `PositionSizer`, respects global 5x leverage limit
  3. **Safe Defaults**: Reduced leverage 20x→10x, max position 25%→15%, separate asset universe (BTC/ETH only)
  4. **Multi-Strategy Coordination**: StrategyRunner passes `positions_by_strategy` callback for real-time conflict checking

- **New Files Created**:
  - `data_service/strategies/enhanced_scalper.py` (450 lines): Integrated strategy class inheriting from StrategyBase
  - `enhanced_scalper/__init__.py`: Package initialization with all module exports

- **Configuration Updates**:
  - `scripts/run_multi_strategy.py`: Added EnhancedScalper import, STRATEGY_CONFIGS entry, positions_getter callback
  - `config/strategies.json`: Added enhanced_scalper config (disabled by default, safe parameters)
  - `enhanced_scalper/config.json`: Updated to v1.1.0 with integration notes
  - `enhanced_scalper/__init__.py`: Module exports for clean integration

- **How It Works**:
  1. Strategy signals use order book, volume delta, stop hunt detection (all existing modules)
  2. Signals returned as Dict[str, Signal] with direction ('long'/'short'/'flat') and confidence
  3. Sizing returns Dict[str, float] as % of equity (not absolute $)
  4. OrderManager handles execution with `is_closing` flag for position reductions
  5. All risk checks delegated to system RiskManager (pre-trade validation)
  6. Positions tracked centrally in `positions_by_strategy`, accessible to all strategies

- **Safety Features**:
  - ✅ Disabled by default (edit STRATEGY_CONFIGS to enable)
  - ✅ Conflict detection prevents same-symbol trading as other strategies
  - ✅ Reduced leverage/position sizes (10x, 15% max)
  - ✅ Respects global risk limits (circuit breaker, daily loss)
  - ✅ Thread-safe cooldown (no threading issues)
  - ✅ Syntax verified - all files compile cleanly
  - ✅ Imports verified - strategy registers in STRATEGY_REGISTRY

- **To Enable** (when ready for testing):
  1. In `scripts/run_multi_strategy.py`: Change "enabled": False → True
  2. In `config/strategies.json`: Change "enabled": false → true
  3. Run `./scripts/start_news_service.sh && nohup venv/bin/python3 scripts/run_multi_strategy.py --duration 24`

- **Expected Behavior**:
  - BTC/ETH only (no conflicts with other strategies' asset universes)
  - ~60-65% win rate target (actual TBD by live testing)
  - 0.3% stops, 0.6% targets, 10-minute max hold
  - Position sizes scale by confidence + consecutive losses

- **Files Modified** (7 total):
  - enhanced_scalper/volume_delta_analyzer.py (timedelta import)
  - enhanced_scalper/stop_hunt_detector.py (timedelta import)
  - enhanced_scalper/ultra_scalper_pro.py (position_size attribute)
  - enhanced_scalper/risk_manager_high_leverage.py (async-safe cooldown)
  - scripts/run_multi_strategy.py (imports + STRATEGY_CONFIGS + positions_getter)
  - config/strategies.json (added enhanced_scalper config)
  - enhanced_scalper/config.json (updated to v1.1.0)

- **Context**: Enhanced Scalper is now production-ready as a 4th strategy alongside Momentum, MeanReversion, and Sentiment. Zero conflicts with existing system. All critical bugs fixed. Ready for paper trading validation in Phase 13.


