# Deep Analysis: Momentum Strategy Performance

## Current Performance (Volatility Sized)
- **Win Rate**: 60.0% (↑ from 56.2%)
- **Avg Win**: $256.73
- **Avg Loss**: $348.95
- **Reward:Risk Ratio**: 0.74:1 (Poor - need >1.3:1)
- **Total Return**: -6.43%
- **Max Drawdown**: 8.98%

## Root Cause Analysis

### 1. **Asymmetric Exit Logic** ⚠️ CRITICAL ISSUE
**Problem**: Winners are cut short, losers run free.

Current implementation:
```python
# TRAILING STOP (Lines 167-180)
if self.highest_profit[symbol] > 0.02:  # Only after 2% profit
    drawdown_from_peak = self.highest_profit[symbol] - abs(pnl_pct)
    if drawdown_from_peak > self.highest_profit[symbol] * 0.5:  # 50% retracement
        direction = 'flat'  # EXIT
```

**Issues**:
- Trailing stop activates ONLY on winning trades (>2% profit)
- **NO explicit stop loss for losing trades** → Losses run uncapped
- 50% retracement is too tight → Cuts winners prematurely
- Result: Avg Win ($257) < Avg Loss ($349)

### 2. **Volatility Sizing Amplification**
**Problem**: Higher leverage magnifies a strategy with negative edge.

- ATR-based sizing creates positions up to 1.9x leverage (seen in logs: `Size=1.9231`)
- With max_position_size capped at 0.50 (effective 2:1 leverage)
- Amplifies the -0.73% baseline loss to -6.43%

### 3. **Cooldown Too Long**
- Current: 30 minutes (optimized from 60)
- In 1h timeframe backtests, this prevents re-entries within same bar
- May miss trend reversal points

## Proposed Refinements

### **Priority 1: Implement Hard Stop Loss** 🔴
**Goal**: Protect capital, improve R:R ratio to >1.0

```python
# Add to calculate_signals after line 100
# HARD STOP LOSS (Mirror trailing stop logic)
if last_sig and last_sig.direction == direction and symbol in self.entry_prices:
    entry_price = self.entry_prices[symbol]
    pnl_pct = (current_price - entry_price) / entry_price if direction == 'long' \
              else (entry_price - current_price) / entry_price
    
    # Stop loss at -2% or -2*ATR (whichever is tighter)
    stop_loss_pct = min(0.02, self.latest_atr.get(symbol, 0.01) * 2)
    
    if pnl_pct < -stop_loss_pct:
        direction = 'flat'
        rationale_parts.append(f"stop loss ({pnl_pct:.2%})")
        if symbol in self.entry_prices: del self.entry_prices[symbol]
        if symbol in self.highest_profit: del self.highest_profit[symbol]
```

### **Priority 2: Widen Trailing Stop** 🟡
**Goal**: Let winners run further

```python
# Modify line 176
if drawdown_from_peak > self.highest_profit[symbol] * 0.30:  # 30% retracement (was 50%)
```

### **Priority 3: Add Take-Profit Target** 🟡
**Goal**: Lock in profits at reasonable levels

```python
# Add after stop loss check
take_profit_pct = 0.03  # 3% profit target
if pnl_pct > take_profit_pct:
    direction = 'flat'
    rationale_parts.append(f"take profit ({pnl_pct:.2%})")
```

### **Priority 4: Reduce Max Position Size** 🟢
**Goal**: Limit drawdown risk while volatility sizing finds edge

```python
# In strategies.json
"max_position_size": 0.30,  # Down from 0.50 (1.5:1 vs 2:1 leverage)
```

### **Priority 5: Dynamic Stop Loss ATR Multiplier** 🟢
**Goal**: Adapt stop loss to market regime

```python
# In strategies.json
"stop_loss_atr_multiplier": 1.5,  # Down from 2.0 
# Tighter stops in volatile markets
```

## Expected Impact

| Metric | Current | Target (After Refinements) |
|--------|---------|---------------------------|
| Avg Win | $257 | $350+ (Let winners run) |
| Avg Loss | $349 | $200- (Hard stop) |
| R:R Ratio | 0.74 | 1.75+ |
| Win Rate | 60% | 55-60% (Stop may reduce slightly) |
| Total Return | -6.43% | **+3% to +8%** |
| Max DD | 8.98% | **<5%** |

## Implementation Order
1. ✅ Add Hard Stop Loss (CRITICAL)
2. ✅ Widen Trailing Stop to 30%
3. ✅ Add 3% Take Profit
4. ✅ Reduce max_position_size to 0.30
5. ✅ Test with backtest
6. 🔄 If Sharpe still <1.0, reduce stop_loss_atr_multiplier to 1.5x
