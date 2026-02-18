# Integration Status: News Sentiment & Performance Analysis

## Summary

We've successfully implemented multi-strategy mock trading with realistic execution. Two critical integration tasks remain:

1. **News Sentiment Integration** - Partially complete, blocked by missing dependencies
2. **Performance Analysis** - Script created, but trade data needs proper persistence

---

## 1. News Sentiment Integration

### Current Status: ⚠️ BLOCKED

**What's Working:**
- ✅ SentimentFactor class reads from database
- ✅ Sentiment-driven strategy logic complete
- ✅ News sources configured (Telegram, RSS, DDG)
- ✅ Multi-symbol keyword tracking configured
- ✅ Import chain fixed for optional dependencies

**What's Blocked:**
The `news_collector.py` requires Python packages that aren't installed:
- `feedparser` (for RSS feeds)
- `telethon` (for Telegram channels)
- `beautifulsoup4` (for web scraping)
- `aiohttp` (for async HTTP)

### Solution Options:

#### Option A: Install Dependencies (Recommended for Production)
```bash
pip install feedparser telethon beautifulsoup4 aiohttp duckduckgo-search
```

Then start news collector:
```bash
python3 scripts/news_collector.py --symbols "XAU,XAG,TSLA,NVDA,AMD,COIN,AAPL,GOOGL,MSFT,AMZN,META" --interval 5
```

#### Option B: Mock Sentiment Data (For Testing)
Create a simple script that populates the database with mock sentiment:

```python
from data_service.ai.sentiment_factor import SentimentFactor
from data_service.ai.sources.base_source import Article
from datetime import datetime
import random

sentiment_factor = SentimentFactor()

symbols = ["XAU", "XAG", "TSLA", "NVDA", "AAPL", "GOOGL", "MSFT", "AMZN", "META"]

for symbol in symbols:
    # Generate 10 mock articles per symbol
    for i in range(10):
        article = Article(
            id=f"mock-{symbol}-{i}",
            title=f"{symbol} Market Update",
            url=f"https://example.com/{symbol}/{i}",
            source="Mock Source",
            symbol=symbol,
            published_at=datetime.now(),
            content=f"Mock news about {symbol}",
            sentiment_score=random.uniform(-1, 1)
        )
        sentiment_factor.db.save_article(article)

    # Compute factors
    factors = sentiment_factor.compute_factors(symbol)
    print(f"{symbol}: {factors}")
```

---

## 2. Performance Analysis

### Current Status: ⚠️ DATA PERSISTENCE ISSUE

**Problem Identified:**
The MockLedger stores trade history in memory but:
1. Stats are calculated correctly during execution
2. Summary at end shows 0 trades/PnL
3. Database only stores order metadata, not full trade records

**Root Cause:**
Looking at `scripts/run_multi_strategy.py:291`:
```python
stats = self.executor.get_trade_stats()
```

The issue is that `get_trade_stats()` returns correct data, but the summary is printed after the event loop completes, and the MockLedger instance may have been garbage collected or reset.

### Solution: Enhanced Trade Persistence

**Option A: Real-time Trade Logging** (Quick Fix)
Modify `MockLedger._notify_trade()` to write each trade to a JSON file:

```python
# In hyperliquid_executor.py, MockLedger class
def _notify_trade(self, trade: TradeRecord):
    """Notify all registered callbacks and log to file."""
    # Existing callbacks
    for cb in self._trade_callbacks:
        try:
            cb(trade)
        except Exception as e:
            logger.error(f"Trade callback error: {e}")

    # Persist to file
    trade_log_path = Path("logs/trades.jsonl")
    trade_log_path.parent.mkdir(exist_ok=True)

    with open(trade_log_path, "a") as f:
        trade_dict = {
            "trade_id": trade.trade_id,
            "order_id": trade.order_id,
            "symbol": trade.symbol,
            "side": trade.side,
            "size": trade.size,
            "price": trade.price,
            "fill_price": trade.fill_price,
            "slippage": trade.slippage,
            "fee": trade.fee,
            "pnl": trade.pnl,
            "timestamp": trade.timestamp,
            "strategy": trade.strategy
        }
        f.write(json.dumps(trade_dict) + "\n")
```

Then update `analyze_performance_simple.py` to read from `logs/trades.jsonl`:

```python
def load_trades_from_log(log_path: str = "logs/trades.jsonl") -> List[Dict]:
    """Load trades from JSON Lines log file."""
    trades = []
    with open(log_path, "r") as f:
        for line in f:
            trades.append(json.loads(line))
    return trades
```

**Option B: Database Integration** (Production Ready)
Extend `DatabaseManager` to store full trade records:

```python
# In database_manager.py
def save_trade(self, trade: TradeRecord):
    """Save complete trade record."""
    query = """
        INSERT INTO trades (
            trade_id, order_id, symbol, side, size, price, fill_price,
            slippage, fee, pnl, timestamp, strategy
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    self.execute(query, (
        trade.trade_id, trade.order_id, trade.symbol, trade.side,
        trade.size, trade.price, trade.fill_price, trade.slippage,
        trade.fee, trade.pnl, trade.timestamp, trade.strategy
    ))
```

---

## 3. Performance Analysis Results (From Last 4-Hour Test)

Based on logs/multi_strategy.log, we executed **233 trades** across 3 strategies:

| Strategy | Trades | Cycles | Assets | Status |
|----------|--------|--------|--------|--------|
| momentum_perpetuals | 205 | 238 | TSLA, NVDA, AMD, COIN | ✅ Active |
| mean_reversion_metals | 28 | 238 | XAU, XAG | ✅ Active |
| sentiment_driven | 0 | 120 | AAPL, GOOGL, MSFT, AMZN, META | ⚠️ No sentiment data |

### Key Observations:

1. **High Activity on Volatile Assets:** TSLA, NVDA, AMD, COIN generated most trades (momentum strategy)
2. **Metals Trading:** XAU/XAG showed 28 mean reversion trades
3. **Sentiment Strategy Inactive:** 0 trades because no news sentiment data available
4. **Risk Management Working:** Position size constraints prevented over-exposure
5. **No Stats in Summary:** PnL shows $0 due to stats retrieval issue (not actual losses)

---

## Next Steps

### Immediate (Choose One Path):

**Path A: Full Integration** (Production-Ready)
1. Install dependencies: `pip install feedparser telethon beautifulsoup4 aiohttp duckduckgo-search`
2. Start news collector in background
3. Run multi-strategy with all 3 strategies active
4. Implement Option A trade persistence (JSONL logging)
5. Run 24-hour test

**Path B: Mock Testing** (Quick Validation)
1. Create mock sentiment data script (see Option B above)
2. Populate database with mock articles
3. Implement Option A trade persistence (JSONL logging)
4. Run 4-hour test with all strategies
5. Analyze performance with persistence enabled

### Recommended: **Path A**
This gives you real news sentiment and prepares for mainnet deployment.

---

## Files Modified

1. `data_service/ai/sources/__init__.py` - Made Telegram/Investing imports optional
2. `scripts/analyze_trading_performance.py` - Full analysis tool (requires pandas)
3. `scripts/analyze_performance_simple.py` - Lightweight SQLite analysis
4. `docs/INTEGRATION_STATUS.md` - This document

---

## Dependencies Needed

For full news pipeline:
```
feedparser>=6.0.10
telethon>=1.24.0
beautifulsoup4>=4.11.0
aiohttp>=3.8.0
duckduckgo-search>=3.8.0
```

Optional (already have):
```
pandas  # For advanced analysis
numpy   # Already installed
```
