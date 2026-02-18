# Dashboard Feature Roadmap

**Created:** February 7, 2026
**Status:** Planning Phase
**Goal:** Transform the dashboard into a comprehensive, profitable trading cockpit

---

## Current State Summary

The dashboard currently tracks:
- Portfolio equity, leverage, P&L
- Open positions (basic view)
- Recent trades (10 shown)
- Risk metrics (VaR, CVaR, max drawdown)
- Price charts (4 symbols)
- Strategy status (start/stop)
- Sentiment scores + headlines
- System health

**Gap identified:** Backend has significantly more data than frontend displays.

---

## Tier 1: Quick Wins (Data Already Exists)

### 1.1 Equity Curve Chart
- **Effort:** Low (2-3 hours)
- **Value:** High
- **Data source:** `risk_snapshots` table
- **Description:** Line chart showing equity over time with drawdown overlay
- **Implementation:**
  - Add `/api/equity-history` endpoint
  - Use Lightweight Charts area series
  - Show 7d / 30d / 90d / All time views
- [x] **COMPLETED Feb 7, 2026**

### 1.2 Metals Ratio Dashboard
- **Effort:** Low (2-3 hours)
- **Value:** High (directly tied to strategy signals)
- **Data source:** `metals_factors` table
- **Description:** Visualize gold/silver ratio, z-scores, copper/gold ratio
- **Implementation:**
  - Add `/api/metals-factors` endpoint
  - Display current values + historical chart
  - Show when signals trigger
- [x] **COMPLETED Feb 7, 2026**

### 1.3 Funding Rate Monitor
- **Effort:** Low (1-2 hours)
- **Value:** Medium-High
- **Data source:** `HyperliquidFetcher.get_funding_rate()`
- **Description:** Show funding rates for perpetual futures
- **Implementation:**
  - Add funding rates to price tick WebSocket
  - Color code: green (you get paid), red (you pay)
  - Calculate daily funding cost/income
- [x] **COMPLETED Feb 7, 2026**

### 1.4 Expanded Trade History + Export
- **Effort:** Low (3-4 hours)
- **Value:** High
- **Data source:** `trades` table (already has all data)
- **Description:** Full trade journal with filtering and export
- **Implementation:**
  - New `/api/trades/full` endpoint with pagination
  - Filter by strategy, symbol, date range, side
  - CSV/JSON export button
  - P&L breakdown by strategy/asset
- [ ] Not started

### 1.5 Article Deep Dive Modal
- **Effort:** Low (2 hours)
- **Value:** Medium
- **Data source:** `news` table (full content exists)
- **Description:** Click headline → modal with full article + sentiment breakdown
- **Implementation:**
  - Add `/api/news/{id}` endpoint for full article
  - Modal component with sentiment score visualization
  - Highlight sentiment-bearing phrases
- [ ] Not started

### 1.6 Liquidation Distance Warnings
- **Effort:** Low (2-3 hours)
- **Value:** Critical (safety)
- **Data source:** Risk manager calculations
- **Description:** Show how far each position is from liquidation
- **Implementation:**
  - Calculate liquidation price per position
  - Add to positions table as new column
  - Color gradient: green (>15%) → yellow (10-15%) → orange (5-10%) → red (<5%)
  - Pulsing animation on critical positions
- [x] **COMPLETED Feb 7, 2026**

---

## Tier 2: Moderate Effort (New Logic Required)

### 2.1 Position Correlation Heatmap
- **Effort:** Medium (4-5 hours)
- **Value:** High
- **Data source:** Historical returns from candles
- **Description:** Matrix showing correlation between positions
- **Implementation:**
  - Calculate rolling 30-day correlations
  - D3.js or simple CSS grid heatmap
  - Alert when portfolio correlation spikes (diversification breakdown)
- [ ] Not started

### 2.2 Strategy P&L Attribution
- **Effort:** Medium (3-4 hours)
- **Value:** High
- **Data source:** `trades` table with `strategy_name`
- **Description:** Break down daily/weekly P&L by strategy
- **Implementation:**
  - Aggregate P&L by strategy in new endpoint
  - Stacked bar chart or pie chart
  - Show contribution percentage
- [ ] Not started

### 2.3 Win/Loss Streak Tracker
- **Effort:** Low-Medium (2-3 hours)
- **Value:** Medium (psychology)
- **Data source:** `trades` table
- **Description:** Track current and historical streaks
- **Implementation:**
  - Calculate streaks from trade history
  - Display current streak with fire emoji
  - Show best/worst historical streaks
- [ ] Not started

### 2.4 Trade Time Analysis Heatmap
- **Effort:** Medium (4-5 hours)
- **Value:** Medium-High
- **Data source:** `trades` table timestamps
- **Description:** When do you make money?
- **Implementation:**
  - Aggregate P&L by hour of day, day of week
  - Heatmap visualization (7x24 grid)
  - Identify best/worst trading windows
- [ ] Not started

### 2.5 Drawdown Recovery Tracker
- **Effort:** Medium (3-4 hours)
- **Value:** High
- **Data source:** `risk_snapshots` table
- **Description:** Track time spent in drawdowns
- **Implementation:**
  - Calculate drawdown periods from equity curve
  - Show current drawdown duration
  - Historical average recovery time
  - Worst recovery on record
- [ ] Not started

---

## Tier 3: Advanced Features (High Value)

### 3.1 Real-Time Voice Alerts
- **Effort:** Low (1-2 hours)
- **Value:** High
- **Data source:** Trade notifications WebSocket
- **Description:** Hear trades execute via browser speech synthesis
- **Implementation:**
  - Toggle button in top bar
  - Announces trades: "BUY 5 XAU at 1950.00"
  - Announces critical/high severity risk alerts
  - Persists preference to localStorage
- [x] **COMPLETED Feb 7, 2026**

### 3.2 Telegram/Discord Bot Integration
- **Effort:** Medium (4-6 hours)
- **Value:** Very High
- **Data source:** All critical events
- **Description:** Push alerts to phone
- **Implementation:**
  - Create Telegram bot via BotFather
  - Add `/api/alerts/telegram` webhook
  - Configurable alert types:
    - Large position opened
    - Daily P&L threshold hit
    - Risk limit breach
    - News collector down
    - Strategy stopped unexpectedly
- [ ] Not started

### 3.3 Monte Carlo Simulation
- **Effort:** High (6-8 hours)
- **Value:** Very High
- **Data source:** Historical returns
- **Description:** Simulate future scenarios
- **Implementation:**
  - Run 10,000 simulations of future returns
  - Display confidence intervals (5th, 50th, 95th percentile)
  - Interactive time horizon slider
  - Probability of hitting targets/drawdowns
- [ ] Not started

### 3.4 Strategy Parameter Tuning UI
- **Effort:** High (8-10 hours)
- **Value:** Very High
- **Data source:** Strategy config
- **Description:** Adjust parameters without code
- **Implementation:**
  - Load current strategy parameters
  - Sliders/inputs for each tunable param
  - Live preview of how changes affect signals
  - Save to config file
  - Restart strategy with new params
- [ ] Not started

### 3.5 Trade Replay Mode
- **Effort:** High (8-12 hours)
- **Value:** High (learning)
- **Data source:** Historical candles + trades
- **Description:** Watch trades unfold on chart
- **Implementation:**
  - Select date range
  - Playback controls (play, pause, speed)
  - Show entry/exit markers as they happen
  - P&L counter updating in real-time
- [ ] Not started

### 3.6 Custom Alert Builder
- **Effort:** High (10-12 hours)
- **Value:** Very High
- **Data source:** All available metrics
- **Description:** GUI to create custom alerts
- **Implementation:**
  - Alert condition builder (if X > Y then notify)
  - Available conditions:
    - Price thresholds
    - Sentiment thresholds
    - Risk metrics
    - Strategy signals
    - Time-based
  - Multiple notification channels
  - Save/load alert profiles
- [ ] Not started

### 3.7 Automated Daily Report Email
- **Effort:** Medium (4-6 hours)
- **Value:** High
- **Data source:** All metrics
- **Description:** Daily summary email
- **Implementation:**
  - Schedule job at midnight
  - Generate HTML email with:
    - P&L summary
    - Best/worst trades
    - Strategy performance
    - Risk metrics
    - Tomorrow's outlook
  - Use SMTP or SendGrid
- [ ] Not started

### 3.8 Market Regime Detector
- **Effort:** High (8-10 hours)
- **Value:** Very High
- **Data source:** Price data + volatility
- **Description:** Classify current market state
- **Implementation:**
  - Regimes: Trending Up, Trending Down, Ranging, High Vol, Low Vol
  - Use rolling statistics (ADX, ATR, Hurst exponent)
  - Display current regime prominently
  - Optional: Auto-adjust strategy weights
- [ ] Not started

### 3.9 Order Flow Imbalance
- **Effort:** Medium (4-5 hours)
- **Value:** High
- **Data source:** Order book from fetcher
- **Description:** Calculate buy/sell pressure
- **Implementation:**
  - `Imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)`
  - Display as gauge (-1 to +1)
  - Historical imbalance chart
  - Potential signal for short-term direction
- [ ] Not started

---

## Tier 4: Cutting Edge Features

### 4.1 LLM-Powered Trade Explainer
- **Effort:** Medium (4-6 hours)
- **Value:** Very High (understanding)
- **Data source:** All trade context
- **Description:** AI explains why each trade happened
- **Implementation:**
  - After trade, gather context:
    - Strategy that triggered
    - Sentiment values
    - Technical indicators
    - Risk metrics at time of trade
  - Send to GPT-4 API with prompt
  - Display explanation in trade details
- [ ] Not started

### 4.2 Anomaly Detection System
- **Effort:** High (10-12 hours)
- **Value:** Very High
- **Data source:** All metrics
- **Description:** Alert on unusual behavior
- **Implementation:**
  - Track rolling statistics for all metrics
  - Flag when values exceed 2-3 std deviations:
    - Sentiment variance spike
    - Unusual volume
    - Strategy deviating from backtest
    - Correlation breakdown
  - Display anomalies prominently
- [ ] Not started

### 4.3 Live Backtesting Comparison
- **Effort:** Very High (12-16 hours)
- **Value:** High
- **Data source:** Live trades + backtest predictions
- **Description:** Compare live vs expected performance
- **Implementation:**
  - Run backtest logic in parallel with live
  - Track what backtest would have done
  - Show divergence metrics
  - Alert on strategy drift
- [ ] Not started

### 4.4 News Source Reliability Scoring
- **Effort:** Medium (6-8 hours)
- **Value:** High
- **Data source:** `news` + `trades` tables joined
- **Description:** Track which sources lead to profitable trades
- **Implementation:**
  - Correlate news source with trade outcomes
  - Calculate win rate by source
  - Auto-weight sources in sentiment calculation
  - Display reliability scores in news feed
- [ ] Not started

### 4.5 Position Aging Heatmap
- **Effort:** Low-Medium (3-4 hours)
- **Value:** Medium
- **Data source:** Position timestamps
- **Description:** Visualize how long positions have been open
- **Implementation:**
  - Track position open time
  - Color code by age (newer = green, older = yellow/red)
  - Alert on unusually old positions
- [ ] Not started

---

## Implementation Priority Queue

### Phase 1: Safety & Visibility (Week 1)
1. Liquidation Distance Warnings (critical safety)
2. Equity Curve Chart (high impact visualization)
3. Voice Alerts (low effort, high convenience)

### Phase 2: Understanding Performance (Week 2)
4. Strategy P&L Attribution
5. Expanded Trade History + Export
6. Trade Time Analysis Heatmap

### Phase 3: Market Intelligence (Week 3)
7. Funding Rate Monitor
8. Metals Ratio Dashboard
9. Order Flow Imbalance

### Phase 4: Notifications & Automation (Week 4)
10. Telegram Bot Integration
11. Automated Daily Report Email
12. Custom Alert Builder

### Phase 5: Advanced Analytics (Ongoing)
13. Monte Carlo Simulation
14. Market Regime Detector
15. Anomaly Detection

### Phase 6: AI & Learning (Future)
16. LLM Trade Explainer
17. News Source Reliability Scoring
18. Trade Replay Mode

---

## Technical Notes

### Frontend Stack
- Vanilla JS (no framework)
- Lightweight Charts for charting
- WebSocket for real-time updates
- CSS Grid layout

### Backend Stack
- FastAPI
- SQLite database
- WebSocket broadcast loop

### Adding New Features Pattern
1. Add database table/columns if needed
2. Create API endpoint in `dashboard_app.py`
3. Add WebSocket message type if real-time
4. Create frontend component
5. Wire up to existing layout

---

## Progress Tracking

| Feature | Status | Started | Completed |
|---------|--------|---------|-----------|
| Equity Curve | ✅ Complete | Feb 7, 2026 | Feb 7, 2026 |
| Liquidation Warnings | ✅ Complete | Feb 7, 2026 | Feb 7, 2026 |
| Voice Alerts | ✅ Complete | Feb 7, 2026 | Feb 7, 2026 |
| Metals Ratios | ✅ Complete | Feb 7, 2026 | Feb 7, 2026 |
| Funding Rates | ✅ Complete | Feb 7, 2026 | Feb 7, 2026 |
| Trade Export | ⬜ Not Started | - | - |
| Article Modal | ⬜ Not Started | - | - |
| Strategy P&L Attribution | ⬜ Not Started | - | - |
| Trade Time Analysis | ⬜ Not Started | - | - |
| Telegram Bot | ⬜ Not Started | - | - |

**Legend:** ⬜ Not Started | 🟡 In Progress | ✅ Complete

---

**Completed Feb 7, 2026:**
- Equity Curve Chart with period selector (1D/7D/30D/90D/All)
- Liquidation Distance Warnings in positions table (color-coded: green/yellow/orange/red)
- Voice Alerts system with toggle button (announces trades and critical risk alerts)
- Funding Rate Monitor with color-coded rates (green=receive, red=pay) + annualized rates
- Metals Ratio Dashboard with Gold/Silver ratio, Z-score, visual signal bar

**Next Session:** Continue with Tier 2 features (Strategy P&L Attribution, Trade Export, Trade Time Analysis)
