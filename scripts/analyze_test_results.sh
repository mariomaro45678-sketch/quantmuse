#!/bin/bash
# Quick analysis script for 24h test results

echo "================================================================================"
echo "24-HOUR TEST RESULTS ANALYSIS"
echo "================================================================================"
echo ""

# Check if test is still running
if ps -p 2205704 > /dev/null 2>&1; then
    echo "⚠️  Test still running (PID 2205704)"
    echo "   Elapsed: $(ps -p 2205704 -o etime= | xargs)"
    echo ""
else
    echo "✅ Test completed"
    echo ""
fi

# Trade counts by strategy
echo "TRADE COUNTS BY STRATEGY"
echo "--------------------------------------------------------------------------------"
mom_count=$(grep "\[momentum_perpetuals\].*Trade #" logs/prod_24h.log | wc -l)
metals_count=$(grep "\[mean_reversion_metals\].*Trade #" logs/prod_24h.log | wc -l)
sent_count=$(grep "\[sentiment_driven\].*Trade #" logs/prod_24h.log | wc -l)
total=$((mom_count + metals_count + sent_count))

echo "momentum_perpetuals:    $mom_count trades"
echo "mean_reversion_metals:  $metals_count trades"
echo "sentiment_driven:       $sent_count trades"
echo "TOTAL:                  $total trades"
echo ""

# Check for summary in log
echo "FINAL SUMMARY FROM LOG"
echo "--------------------------------------------------------------------------------"
if grep -q "SUMMARY" logs/prod_24h.log; then
    grep -A 30 "SUMMARY" logs/prod_24h.log | tail -35
else
    echo "⚠️  No SUMMARY section found in log"
    echo "   Last 20 lines of log:"
    tail -20 logs/prod_24h.log
fi
echo ""

# News collector status
echo "NEWS COLLECTOR STATUS"
echo "--------------------------------------------------------------------------------"
if ps aux | grep -q "[n]ews_collector"; then
    echo "✅ News collector running"
    ps aux | grep "[n]ews_collector" | awk '{print "   PID:", $2, "| Uptime:", $10}'
else
    echo "❌ News collector NOT running"
    echo "   Last activity:"
    tail -5 logs/news_restart_2125.log 2>/dev/null || echo "   (log not found)"
fi
echo ""

# Sentiment data status
echo "SENTIMENT DATA STATUS"
echo "--------------------------------------------------------------------------------"
echo "Running validation..."
venv/bin/python3 scripts/validate_sentiment.py 2>&1 | grep -A 15 "SENTIMENT FACTORS CHECK"
echo ""

echo "================================================================================"
echo "ANALYSIS COMPLETE"
echo "================================================================================"
