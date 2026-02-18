# Edge Improvements Roadmap

**Created:** 2026-02-09
**Goal:** Systematic implementation of edge-gaining features
**Constraint:** Small capital testing - prioritize free data sources, respect min order sizes

---

## Implementation Order (Dependency-Aware)

### Phase 1: Foundation Layer (Do First)
*These provide data/signals that other features depend on*

| # | Feature | Effort | Status | Files |
|---|---------|--------|--------|-------|
| 1.1 | **Market Regime Detector** | 4h | **DONE** | `data_service/factors/regime_detector.py` |
| 1.2 | **Correlation Tracker** | 2h | **DONE** | `data_service/factors/correlation_tracker.py` |
| 1.3 | **Order Book Imbalance Factor** | 3h | **DONE** | `data_service/factors/orderbook_factors.py` |

**Completed 2026-02-09:**
- Regime Detector: ADX, Hurst exponent, ATR percentile -> 5 regimes with strategy multipliers
- Correlation Tracker: Rolling 30d correlations, high-pair detection, effective exposure calc

**Completed 2026-02-10:**
- Order Book Imbalance: Top 5 levels bid/ask volume ratio, integrated into momentum_perpetuals
  - Boost +10% confidence when imbalance agrees with direction
  - Reduce -15% confidence when imbalance conflicts
  - Wide spread (>0.15%) penalty, reject trades at >0.30% spread

**Why first:** Regime detection and correlation affect ALL strategies. Order book is independent alpha.

---

### Phase 2: Signal Enhancement
*Use Phase 1 outputs to improve signal quality*

| # | Feature | Effort | Status | Files |
|---|---------|--------|--------|-------|
| 2.1 | **Strategy Ensemble Voting** | 2h | **DONE** | `data_service/strategies/ensemble_coordinator.py` |
| 2.2 | **News Source Reliability Scoring** | 3h | **DONE** | `data_service/ai/source_reliability.py` |
| 2.3 | **Dynamic Spread Filter** | 1h | *Covered by 1.3* | `data_service/factors/orderbook_factors.py` |

**Completed 2026-02-10:**
- Ensemble Voting: Cross-strategy signal coordination
  - 2 strategies agree: 1.15x confidence boost
  - 3 strategies agree: 1.30x confidence boost
  - Conflict resolution: higher confidence wins (with penalty), or flat if both >60%
  - Portfolio sentiment tracking (bullish/bearish/mixed)

- News Source Reliability Scoring: Dynamic source weighting
  - Tracks per-source: hit_rate, avg_return, latency_score
  - Score = 0.5*hit_rate + 0.3*norm_return + 0.2*latency
  - Dynamic weight range: 0.5x to 1.0x (scaled to 0.85-1.2 in sentiment calc)
  - Auto-records outcomes when sentiment_driven trades close
  - Backfill from historical trades available
  - Test: `scripts/test_source_reliability.py`

- Dynamic Spread Filter: Already implemented in Order Book Imbalance (1.3)
  - Thresholds: 0.05% normal, 0.15% wide (-25%), 0.30% reject

---

### Phase 3: Execution Optimization
*Better entries = lower costs = higher returns*

| # | Feature | Effort | Dependencies | Files to Create/Modify |
|---|---------|--------|--------------|------------------------|
| 3.1 | **Entry Timing (Pullback Wait)** | 3h | Regime | **DONE** `data_service/executors/entry_timing.py` |
| 3.2 | **Regime-Aware Position Sizing** | 2h | Regime, Correlation | **DONE** `data_service/risk/dynamic_sizer.py` |
| 3.3 | **Correlation-Aware Risk Limits** | 2h | Correlation | *Covered by 3.2* |

**Completed 2026-02-10:**
- Entry Timing Optimization: Better average entry prices
  - Entry strategies: IMMEDIATE (>85% confidence), LIMIT_WAIT (default), PULLBACK_WAIT (sentiment)
  - Limit orders: 0.1% better than signal price
  - Pullback wait: 30% retracement of initial move, max 30 min
  - Chase logic: Market order if price moves >0.3% against us or timeout
  - Integrated into run_multi_strategy.py with shared EntryOptimizer
  - Test: `scripts/test_entry_timing.py`

- Regime-Aware Position Sizing: Dynamic position sizing
  - Strategy-specific multipliers: TRENDING boosts momentum (1.2x), reduces mean-rev (0.6x)
  - HIGH_VOL reduces all (0.7x overall)
  - Correlation-aware: Reduces size when correlated positions exist
  - Respects min order ($10) and max position (25% of equity)
  - Signal confidence scaling: 0.5-1.0x based on confidence
  - Integrated into run_multi_strategy.py with shared DynamicSizer
  - Test: `scripts/test_dynamic_sizer.py`

- Correlation-Aware Risk Limits: Included in DynamicSizer
  - Tracks correlated positions and limits effective exposure
  - Max correlated exposure: 40% for high-correlation group
  - Correlation multiplier reduces size when adding to correlated portfolio

---

### Phase 4: External Data Sources (Free First)
*Additional alpha from external information*

| # | Feature | Effort | Status | Files |
|---|---------|--------|--------|-------|
| 4.1 | **Economic Calendar** | 3h | **DONE** | `data_service/ai/sources/economic_calendar.py` |
| 4.2 | **Twitter/X Scraping** | 4h | *Researched - $200+/mo* | Research notes below |
| 4.3 | **Options Flow** | 4h | *Researched - $48/mo* | Research notes below |

**Completed 2026-02-10:**
- Economic Calendar: Forex Factory scraper with residential proxy support
  - Scrapes MEDIUM/HIGH/CRITICAL impact USD events
  - 6-hour cache to minimize requests
  - Uses cloudscraper + proxies to bypass Cloudflare
  - Trading integration: 50% position reduction 1hr before HIGH events
  - Event window: No new entries during event (15min window)
  - Test: `scripts/test_economic_calendar.py`

**Research Notes (4.2 Twitter/X):**
- X API Basic tier: $200/mo (15,000 tweets read)
- X API Pro tier: $5,000/mo (1M tweets read)
- Pay-per-use beta: Credit-based, waiting list
- Monitoring 10-20 accounts at 60s polling: 648k requests/mo -> Basic won't cover
- Nitter: Fragile, requires real X accounts in 2026
- **Decision:** Skip for now - high cost, questionable ROI for small capital

**Research Notes (4.3 Options Flow):**
- Unusual Whales: $48/mo with API access
- Barchart: Free but scrape-only, delayed
- OptionStrat: Free tier has 15min delay
- Note: Options flow is stocks-only, not directly applicable to crypto
- Can use as proxy signal for correlated assets (COIN, MSTR, BTC ETFs)
- **Decision:** Consider later when capital grows

---

### Phase 5: Adaptive Systems
*Self-improving parameters based on performance*

| # | Feature | Effort | Status | Files |
|---|---------|--------|--------|-------|
| 5.1 | **Adaptive Parameter Tuning** | 5h | **DONE** | `data_service/strategies/parameter_adapter.py` |

**Completed 2026-02-11:**
- Adaptive Parameter Tuning: Automatically tunes strategy parameters based on 30-day rolling performance
  - Tracks parameter snapshots at trade time for attribution
  - Computes rolling performance (Sharpe, win rate, PnL) per parameter set
  - Slowly shifts toward better values (max 10% change per week)
  - Weekly cooldown to prevent overfitting
  - Minimum 20 trades required before adaptation
  - Parameters tuned: RSI thresholds, momentum threshold, funding threshold, ADX threshold, etc.
  - Database tables: parameter_snapshots, parameter_performance, parameter_adjustments, active_parameters
  - Test: `scripts/test_parameter_adapter.py`

---

## Detailed Specifications

### 1.1 Market Regime Detector
```
Regimes: TRENDING_UP | TRENDING_DOWN | RANGING | HIGH_VOL | LOW_VOL

Inputs:
- ADX (trend strength): >25 = trending
- ATR % (volatility): compare to 20-day avg
- Hurst exponent: >0.5 = trending, <0.5 = mean-reverting

Output:
{
  "regime": "RANGING",
  "confidence": 0.75,
  "adx": 18.5,
  "volatility_percentile": 0.35,
  "hurst": 0.42
}

Strategy Impact:
- RANGING: Boost mean_reversion, suppress momentum
- TRENDING: Boost momentum, suppress mean_reversion
- HIGH_VOL: Reduce all position sizes by 30%
```

### 1.2 Correlation Tracker
```
Track rolling 30-day correlation matrix for:
- All active symbols
- Update daily

Output:
{
  "XAU_XAG": 0.89,
  "NVDA_AMD": 0.82,
  "TSLA_META": 0.45,
  ...
  "high_correlation_pairs": [("XAU", "XAG"), ("NVDA", "AMD")]
}

Risk Impact:
- If corr > 0.7: Treat as 1.5x exposure (not 2x)
- Warn if portfolio correlation > 0.6 overall
```

### 1.3 Order Book Imbalance
```
Formula: imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)

Levels to track: Top 5 bid/ask levels

Signal interpretation:
- imbalance > +0.3: Bullish pressure (more buyers)
- imbalance < -0.3: Bearish pressure (more sellers)

Integration:
- Add as factor to momentum_perpetuals
- Boost confidence by 0.10 if imbalance agrees with direction
- Reduce confidence by 0.15 if imbalance conflicts
```

### 2.1 Strategy Ensemble Voting
```
When multiple strategies signal same direction:
- 2 strategies agree: confidence *= 1.15
- 3 strategies agree: confidence *= 1.30

When strategies conflict:
- Take the higher confidence signal
- OR go flat if both > 0.6 confidence (uncertainty)

Implementation:
- Run after all strategies generate signals
- Adjust final position sizing based on agreement
```

### 2.2 News Source Reliability Scoring
```
Track per-source metrics:
- hit_rate: % of signals followed by correct direction
- avg_return: average return when trading source's signal
- latency_advantage: how early vs other sources

Score = 0.5 * hit_rate + 0.3 * normalized_return + 0.2 * latency_score

Apply as sentiment weight multiplier:
- source_weight = 0.5 + (reliability_score * 0.5)
- Range: 0.5x to 1.0x weight
```

### 2.3 Dynamic Spread Filter
```
Monitor real-time spread:
spread_pct = (best_ask - best_bid) / mid_price

Thresholds:
- spread < 0.05%: Normal, no adjustment
- spread 0.05-0.15%: Reduce confidence by 10%
- spread > 0.15%: Reduce confidence by 25%
- spread > 0.30%: Skip trade entirely

Also track spread history for anomaly detection.
```

### 3.1 Entry Timing Optimization
```
Instead of immediate entry on signal:

For LONG signals:
1. Set limit order 0.1% below current price
2. If not filled in 5 min, market order
3. If price moves up >0.3% before fill, chase with market

For sentiment signals specifically:
- Wait for first pullback after news spike
- Pullback = price retraces 30% of initial move
- Max wait: 30 minutes, then market order

Benefit: Better average entry price
```

### 3.2 Regime-Aware Position Sizing
```
Base size from strategy, then adjust:

TRENDING regime:
- Momentum strategies: size *= 1.2
- Mean reversion: size *= 0.6

RANGING regime:
- Momentum strategies: size *= 0.7
- Mean reversion: size *= 1.3

HIGH_VOL regime:
- All strategies: size *= 0.7

Also respect:
- Min order notional: $10
- Current equity constraints
```

### 3.3 Correlation-Aware Risk Limits
```
Current: Each position has independent limit

New: Correlated positions share limits

If corr(A, B) > 0.7:
  combined_exposure = abs(pos_A) + abs(pos_B) * corr

Example:
- XAU position: 20%
- XAG position: 20%
- Correlation: 0.85
- Effective exposure: 20% + (20% * 0.85) = 37%
- NOT 40% as currently calculated

Apply portfolio-level limit to effective exposure.
```

### 4.1 Economic Calendar (Free Sources)
```
Free APIs:
- Forex Factory (scrape)
- Investing.com (scrape)
- TradingEconomics (limited free tier)

Events to track:
- FOMC meetings (8/year)
- CPI/PPI releases
- NFP (first Friday)
- GDP releases
- Fed speeches

Action:
- 1 hour before HIGH impact event: reduce position sizes 50%
- During event window: no new entries
- After event: normal trading resumes
```

### 4.2 Twitter/X Integration (Free Methods)
```
Options:
1. Nitter instances (free, rate limited)
2. RSS bridges for key accounts
3. Selenium scraping (slow but free)

Key accounts for our symbols:
- @zaborhedge (Walter Bloomberg style)
- @DeItaone (fast news)
- @elikirist (gold/metals)
- @unusual_whales (options flow)

Parse for:
- Ticker mentions ($TSLA, $NVDA)
- Sentiment keywords
- Breaking news patterns
```

### 4.3 Options Flow (Free Research)
```
Free sources to investigate:
- Barchart.com unusual options
- Yahoo Finance options chain
- Finviz options screener

If viable, track:
- Unusual volume (>2x avg)
- Large single trades (>$1M premium)
- Put/call ratio extremes

Signal: High call volume + bullish = confidence boost
```

### 5.1 Adaptive Parameter Tuning
```
Parameters to adapt:
- RSI thresholds (currently 30/70)
- Momentum threshold (currently 0.3)
- Funding threshold (currently 0.05%)
- Confidence minimums

Method:
- Track 30-day rolling performance per parameter set
- Slowly shift toward better-performing values
- Max change: 10% per week (avoid overfitting)

Example:
If RSI 25/75 outperformed RSI 30/70 by >5% over 30 days:
  new_oversold = 0.9 * 30 + 0.1 * 25 = 29.5
```

---

## Priority Matrix

```
                    HIGH IMPACT
                        ^
                        |
    [Regime Detector]   |   [Order Book Imbalance]
    [Ensemble Voting]   |   [Economic Calendar]
                        |
  LOW EFFORT -----------+----------- HIGH EFFORT
                        |
    [Spread Filter]     |   [Twitter Integration]
    [Correlation Risk]  |   [Adaptive Parameters]
                        |
                        v
                    LOW IMPACT
```

**Recommended Start:** Regime Detector (1.1) - foundational, affects everything else

---

## Success Metrics

| Feature | Metric | Target |
|---------|--------|--------|
| Regime Detector | Strategy performance in correct regime | +20% vs wrong regime |
| Order Book Imbalance | Win rate when imbalance confirms | >55% |
| Ensemble Voting | Sharpe ratio improvement | +0.15 |
| News Reliability | Sentiment signal accuracy | +10% |
| Spread Filter | Slippage reduction | -0.05% avg |
| Entry Timing | Average entry improvement | 0.1% better |
| Economic Calendar | Drawdown during events | -30% |
| Adaptive Parameters | Rolling 30d Sharpe | Monotonic improvement |

---

## File Structure After Implementation

```
data_service/
├── factors/
│   ├── factor_calculator.py      (existing)
│   ├── metals_factors.py         (existing)
│   ├── regime_detector.py        (DONE - Phase 1)
│   ├── correlation_tracker.py    (DONE - Phase 1)
│   └── orderbook_factors.py      (DONE - Phase 1, includes spread filter)
├── strategies/
│   ├── momentum_perpetuals.py    (modified - order book integration)
│   ├── sentiment_driven.py       (modified - reliability integration)
│   ├── mean_reversion_metals.py  (modify)
│   ├── ensemble_coordinator.py   (DONE - Phase 2)
│   └── parameter_adapter.py      (DONE - Phase 5)
├── storage/
│   └── order_storage.py          (modified - reliability hook)
├── executors/
│   ├── order_manager.py          (existing)
│   ├── hyperliquid_executor.py   (existing)
│   └── entry_timing.py           (DONE - Phase 3)
├── risk/
│   ├── risk_manager.py           (existing)
│   ├── position_sizer.py         (existing)
│   └── dynamic_sizer.py          (DONE - Phase 3)
├── ai/
│   ├── sentiment_factor.py       (modified - dynamic weights)
│   ├── source_reliability.py     (DONE - Phase 2)
│   └── sources/
│       ├── economic_calendar.py  (DONE - Phase 4)
│       ├── twitter_source.py     (Skipped - too expensive)
│       └── options_flow.py       (Deferred - $48/mo when capital grows)
scripts/
├── test_regime_detector.py       (DONE)
├── test_correlation_tracker.py   (DONE)
├── test_economic_calendar.py     (DONE)
├── test_orderbook_factors.py     (DONE)
├── test_source_reliability.py    (DONE)
├── test_entry_timing.py          (DONE)
├── test_dynamic_sizer.py         (DONE)
└── test_parameter_adapter.py     (DONE - Phase 5)
```

---

**Phase 1 Complete!** **Phase 2 Complete!** **Phase 3 Complete!** **Phase 4.1 Complete!** **Phase 5.1 Complete!**

All planned phases are complete! Remaining items (4.2 Twitter/X, 4.3 Options Flow) are deferred due to cost.

Edge Improvements Roadmap: **COMPLETE** (2026-02-11)
