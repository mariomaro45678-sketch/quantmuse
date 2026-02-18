# HyperLiquid Ultra Scalper Pro - Quick Reference

## Files Overview

| File | Purpose | Lines | Key Classes |
|------|---------|-------|-------------|
| `ultra_scalper_pro.py` | Main strategy | 550 | HyperLiquidUltraScalper, MicrostructureSignal, TradePosition |
| `orderbook_analyzer.py` | Order book analysis | 350 | OrderBookMicrostructureAnalyzer, MicrostructureMetrics |
| `volume_delta_analyzer.py` | Volume delta | 450 | VolumeDeltaAnalyzer, DeltaMetrics, FootprintMetrics |
| `stop_hunt_detector.py` | Stop hunt detection | 300 | StopHuntDetector, HuntSignal |
| `risk_manager_high_leverage.py` | Risk management | 400 | HighLeverageRiskManager, PositionRisk, RiskState |
| `config.json` | Configuration | 100 | - |

## Key Parameters (config.json)

### Risk Management
```json
{
  "leverage": 20.0,
  "stop_loss_pct": 0.003,        // 0.3% = 6% account at 20x
  "take_profit_pct": 0.006,      // 0.6% = 2:1 R:R
  "daily_loss_limit_pct": 0.05,  // 5% daily stop
  "circuit_breaker_pct": 0.10,   // 10% max drawdown
  "max_consecutive_losses": 3,   // Then 5-min cooldown
  "cooldown_minutes": 5
}
```

### Signal Thresholds
```json
{
  "min_obi": 0.50,               // 50% imbalance required
  "min_delta": 1000,             // Min delta units
  "min_confidence": 0.65,        // 65% confidence minimum
  "max_spread_pct": 0.15,        // Max 0.15% spread
  "min_liquidity_score": 0.70    // 0-1 liquidity score
}
```

## Signal Quality Matrix

| OBI | Delta | Spread | Confidence | Quality |
|-----|-------|--------|------------|---------|
| >0.50 | >1000 | <0.15% | >0.65 | STRONG |
| 0.40-0.50 | 500-1000 | 0.15-0.20% | 0.55-0.65 | MODERATE |
| <0.40 | <500 | >0.20% | <0.55 | WEAK/INVALID |

## Entry Checklist

### Long Entry Requirements:
- [ ] OBI filtered > +0.50
- [ ] Delta 1m > +1000
- [ ] No divergence detected
- [ ] Spread < 0.15%
- [ ] Liquidity score > 0.70
- [ ] Confidence > 0.65
- [ ] Not in cooldown
- [ ] <3 open positions
- [ ] Daily loss < 5%

### Short Entry Requirements:
- [ ] OBI filtered < -0.50
- [ ] Delta 1m < -1000
- [ ] No divergence detected
- [ ] Spread < 0.15%
- [ ] Liquidity score > 0.70
- [ ] Confidence > 0.65
- [ ] Not in cooldown
- [ ] <3 open positions
- [ ] Daily loss < 5%

## Exit Rules

| Type | Trigger | Action |
|------|---------|--------|
| **Stop Loss** | -0.3% from entry | Close immediately |
| **Take Profit** | +0.6% from entry | Close immediately |
| **Time Stop** | 10 minutes elapsed | Close at market |
| **Breakeven** | +0.4% then pullback | Move stop to entry |
| **Trailing** | +0.5% then -0.2% | Trailing stop active |

## Risk Limits

| Limit | Value | Action When Hit |
|-------|-------|-----------------|
| **Daily Loss** | 5% | Halt trading for day |
| **Circuit Breaker** | 10% drawdown | Close all, stop bot |
| **Consecutive Losses** | 3 | 5-minute cooldown |
| **Max Positions** | 3 | Reject new signals |
| **Position Size** | 25% of account | Kelly-based sizing |

## Performance Targets

| Metric | Target | Minimum |
|--------|--------|---------|
| Win Rate | 60-65% | 55% |
| Avg Win | 0.6-0.9% | 0.5% |
| Avg Loss | 0.3% | 0.35% |
| Risk:Reward | 1:2 | 1:1.5 |
| Sharpe | >2.0 | >1.5 |
| Max DD | <8% | <12% |
| Trades/Day | 8-20 | 5 |

## Code Snippets

### Generate Signal
```python
signal = await scalper.generate_signal('BTC', market_data, 10000)
if signal and signal.is_valid():
    print(f"Signal: {signal.direction.name}")
    print(f"Confidence: {signal.confidence:.2f}")
    print(f"Entry: ${signal.entry_price:.2f}")
```

### Check Order Book
```python
metrics = orderbook_analyzer.analyze(snapshot)
if metrics.is_valid and metrics.obi_filtered > 0.50:
    print(f"Bullish OBI: {metrics.obi_filtered:.2f}")
```

### Calculate Delta
```python
for trade in trades:
    analyzer.process_tick(create_tick_from_trade(trade))
delta = analyzer.calculate_delta_metrics()
print(f"Delta 1m: {delta.delta_1m:.0f}")
```

### Detect Stop Hunt
```python
hunt = detector.detect_stop_hunt('BTC', price, timestamp, volume)
if hunt and hunt.is_valid_fade():
    print(f"Fade {hunt.fade_direction} at ${hunt.entry_price:.2f}")
```

### Position Sizing
```python
size, risk = risk_manager.calculate_position_size(
    'BTC', 'long', 50000, 0.8
)
print(f"Size: ${size:,.2f}, Risk: {risk.risk_pct:.2%}")
```

## Common Issues

### Issue: High Slippage
**Solution**: Check spread before entry, increase threshold to 0.20%

### Issue: Low Win Rate
**Solution**: Increase confidence to 0.70, increase OBI to 0.55

### Issue: Many Small Losses
**Solution**: Tighten stop to 0.25%, reduce position size

### Issue: Missed Good Trades
**Solution**: Lower OBI to 0.45, lower confidence to 0.60

### Issue: Circuit Breaker Triggered
**Solution**: Review last 20 trades, reduce leverage to 10x temporarily

## Debug Commands

```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Test single component
analyzer = OrderBookMicrostructureAnalyzer()
metrics = analyzer.analyze(test_snapshot)
print(metrics.to_dict())

# Validate signal
signal = await scalper.analyze_microstructure('BTC', test_data)
print(f"Valid: {signal.is_valid()}")
print(f"Quality: {signal.quality}")
```

## Support Files

- `IMPLEMENTATION_GUIDE.md` - Full implementation details
- `README.md` - Strategy overview and usage
- `config.json` - All configuration parameters
- This file - Quick reference

## Emergency Contacts

If strategy behaving unexpectedly:
1. Check `risk_manager.get_risk_report()`
2. Review last 10 trades in `scalper.trade_history`
3. Verify no circuit breaker triggered
4. Check exchange connection status

---
**Version**: 1.0 | **Last Updated**: 2026-02-13
