#!/bin/bash
# Quick Status Check - Run this to get instant system status

echo "═══════════════════════════════════════════════════════════════"
echo "  QUANTMUSE SYSTEM STATUS - $(date)"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Check processes
echo "🔍 RUNNING PROCESSES:"
echo "─────────────────────"
TEST_PID=$(ps aux | grep "run_multi_strategy.py --duration 24" | grep -v grep | awk '{print $2}')
NEWS_PID=$(ps aux | grep "news_collector.py" | grep -v grep | awk '{print $2}')

if [ -n "$TEST_PID" ]; then
    echo "✅ Multi-Strategy Test: Running (PID: $TEST_PID)"
else
    echo "❌ Multi-Strategy Test: NOT RUNNING"
fi

if [ -n "$NEWS_PID" ]; then
    echo "✅ News Collector: Running (PID: $NEWS_PID)"
else
    echo "❌ News Collector: NOT RUNNING (CRITICAL!)"
fi

echo ""

# Trade counts
echo "📊 TRADE STATISTICS:"
echo "─────────────────────"
TOTAL=$(grep -c "Trade #" logs/prod_24h.log 2>/dev/null || echo "0")
MOMENTUM=$(grep -c "momentum_perpetuals.*Trade" logs/prod_24h.log 2>/dev/null || echo "0")
METALS=$(grep -c "mean_reversion_metals.*Trade" logs/prod_24h.log 2>/dev/null || echo "0")
SENTIMENT=$(grep -c "sentiment_driven.*Trade" logs/prod_24h.log 2>/dev/null || echo "0")

echo "Total Trades: $TOTAL"
echo "  - momentum_perpetuals: $MOMENTUM"
echo "  - mean_reversion_metals: $METALS"
echo "  - sentiment_driven: $SENTIMENT"

if [ "$SENTIMENT" -eq 0 ]; then
    echo "  ⚠️  sentiment_driven has NOT traded yet!"
fi

echo ""

# Test progress
echo "⏱️  TEST PROGRESS:"
echo "─────────────────────"
if [ -f logs/prod_24h.log ]; then
    START_TIME=$(head -20 logs/prod_24h.log | grep "STARTED" | head -1)
    if [ -n "$START_TIME" ]; then
        echo "Started: ~10:08 CET"
        echo "Current: $(date '+%H:%M CET')"
        # Simple calculation - test started at 10:08, calculate hours
        CURRENT_HOUR=$(date +%H)
        CURRENT_MIN=$(date +%M)
        ELAPSED=$(( ($CURRENT_HOUR - 10) * 60 + $CURRENT_MIN - 8 ))
        REMAINING=$(( 1440 - $ELAPSED ))
        echo "Elapsed: ~$(($ELAPSED / 60))h $(($ELAPSED % 60))m"
        echo "Remaining: ~$(($REMAINING / 60))h $(($REMAINING % 60))m"
    fi
fi

echo ""

# News collector status
echo "📰 NEWS COLLECTOR:"
echo "─────────────────────"
if [ -f logs/news_restart_2125.log ]; then
    LAST_CYCLE=$(tail -100 logs/news_restart_2125.log | grep "Cycle.*complete" | tail -1)
    if [ -n "$LAST_CYCLE" ]; then
        echo "Latest: $LAST_CYCLE"
    else
        echo "⚠️  No completed cycles yet (may still be initializing)"
    fi
fi

echo ""

# Sentiment signals
echo "🎯 SENTIMENT SIGNALS:"
echo "─────────────────────"
echo "Run this for live signals:"
echo "  venv/bin/python3 scripts/validate_sentiment.py"

echo ""

# Quick actions
echo "🔧 QUICK ACTIONS:"
echo "─────────────────────"
echo "Watch for sentiment trades:"
echo "  tail -f logs/prod_24h.log | grep sentiment_driven"
echo ""
echo "Check sentiment signals now:"
echo "  venv/bin/python3 scripts/validate_sentiment.py"
echo ""
echo "Restart news collector (if crashed):"
echo "  pkill -f news_collector"
echo "  nohup venv/bin/python scripts/news_collector.py --symbols \"XAU,XAG,TSLA,NVDA,AMD,COIN,AAPL,GOOGL,MSFT,AMZN,META\" --interval 5 > logs/news_restart_new.log 2>&1 &"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "For full details: cat HANDOFF.md"
echo "═══════════════════════════════════════════════════════════════"
