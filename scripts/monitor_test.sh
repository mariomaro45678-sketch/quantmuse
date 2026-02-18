#!/bin/bash
#
# Monitor ongoing multi-strategy test and news collector
# Usage: ./scripts/monitor_test.sh [--watch]
#

WATCH_MODE=false
if [[ "$1" == "--watch" ]]; then
    WATCH_MODE=true
fi

show_status() {
    clear
    echo "========================================"
    echo "QuantMuse System Monitor"
    echo "$(date)"
    echo "========================================"
    echo ""

    # Process Status
    echo "📊 PROCESS STATUS"
    echo "----------------------------------------"
    NEWS_PID=$(pgrep -f "news_collector.py" | head -1)
    WATCH_PID=$(pgrep -f "watchdog.py --daemon" | head -1)
    TEST_PID=$(pgrep -f "run_multi_strategy.py" | head -1)

    if [[ -n "$NEWS_PID" ]]; then
        echo "✅ News Collector:  RUNNING (PID $NEWS_PID)"
    else
        echo "❌ News Collector:  NOT RUNNING"
    fi

    if [[ -n "$WATCH_PID" ]]; then
        echo "✅ Watchdog:        RUNNING (PID $WATCH_PID)"
    else
        echo "⚠️  Watchdog:        NOT RUNNING"
    fi

    if [[ -n "$TEST_PID" ]]; then
        echo "✅ Multi-Strategy:  RUNNING (PID $TEST_PID)"
    else
        echo "❌ Multi-Strategy:  NOT RUNNING"
    fi
    echo ""

    # News Collector Health
    echo "📰 NEWS COLLECTOR HEALTH"
    echo "----------------------------------------"
    if [[ -f logs/news_collector_health.json ]]; then
        python3 -c "
import json
from datetime import datetime, timedelta

with open('logs/news_collector_health.json') as f:
    health = json.load(f)

status_emoji = '✅' if health['status'] == 'healthy' else '⚠️'
print(f\"{status_emoji} Status:       {health['status']}\")
print(f\"   Cycles:       {health['cycles']}\")
print(f\"   Articles:     {health['articles_processed']}\")
print(f\"   Failures:     {health['consecutive_failures']}\")

# Parse timestamp
ts = datetime.fromisoformat(health['timestamp'])
now = datetime.now()
age = (now - ts).total_seconds()
hours = int(age // 3600)
mins = int((age % 3600) // 60)
secs = int(age % 60)

if age < 120:
    age_emoji = '✅'
elif age < 600:
    age_emoji = '⚠️'
else:
    age_emoji = '❌'

print(f\"{age_emoji} Last update:  {hours}h {mins}m {secs}s ago\")

print(\"   Sources:\")
for source, stats in health.get('source_stats', {}).items():
    emoji = '✅' if stats['failures'] == 0 else '❌'
    print(f\"     {emoji} {source}: {stats['fetched']} fetched, {stats['failures']} failures\")
" 2>/dev/null || echo "⚠️ Health file error"
    else
        echo "⚠️ No health file found"
    fi
    echo ""

    # Multi-Strategy Test Progress
    echo "🎯 MULTI-STRATEGY TEST"
    echo "----------------------------------------"
    LATEST_LOG=$(ls -t logs/prod_test_*.log 2>/dev/null | head -1)
    if [[ -n "$LATEST_LOG" ]]; then
        echo "Log: $LATEST_LOG"
        echo ""

        # Trade counts by strategy
        echo "Trade Counts:"
        for strategy in momentum_perpetuals mean_reversion_metals sentiment_driven; do
            count=$(grep -c "\[$strategy\] Trade #" "$LATEST_LOG" 2>/dev/null || echo "0")
            if [[ "$strategy" == "sentiment_driven" ]]; then
                if [[ "$count" -gt 0 ]]; then
                    echo "  ✅ $strategy: $count trades"
                else
                    echo "  ⚠️  $strategy: $count trades (waiting for news)"
                fi
            else
                echo "  ✅ $strategy: $count trades"
            fi
        done
        echo ""

        # Recent activity (last 5 trades)
        echo "Recent Trades (last 5):"
        grep -E "\[.*\] Trade #" "$LATEST_LOG" 2>/dev/null | tail -5 | while read line; do
            echo "  $line"
        done

        # Errors/Warnings
        ERROR_COUNT=$(grep -cE "ERROR|Exception" "$LATEST_LOG" 2>/dev/null || echo "0")
        if [[ "$ERROR_COUNT" -gt 0 ]]; then
            echo ""
            echo "⚠️  Errors detected: $ERROR_COUNT"
            echo "Recent errors:"
            grep -E "ERROR|Exception" "$LATEST_LOG" 2>/dev/null | tail -3 | while read line; do
                echo "  $line"
            done
        fi
    else
        echo "⚠️ No test log found"
    fi
    echo ""

    # System Resources
    echo "💻 SYSTEM RESOURCES"
    echo "----------------------------------------"
    if [[ -n "$NEWS_PID" ]]; then
        ps -p "$NEWS_PID" -o %cpu,%mem,etime 2>/dev/null | tail -1 | \
            awk '{printf "   News Collector:  CPU=%s%% MEM=%s%% TIME=%s\n", $1, $2, $3}'
    fi
    if [[ -n "$TEST_PID" ]]; then
        ps -p "$TEST_PID" -o %cpu,%mem,etime 2>/dev/null | tail -1 | \
            awk '{printf "   Multi-Strategy:  CPU=%s%% MEM=%s%% TIME=%s\n", $1, $2, $3}'
    fi
    echo ""

    echo "========================================"
    echo "Commands:"
    echo "  ./scripts/news_status.sh     - News collector status"
    echo "  tail -f $LATEST_LOG - Follow test log"
    echo "  ./scripts/stop_news_service.sh - Stop services"
    echo "========================================"
}

if [[ "$WATCH_MODE" == true ]]; then
    while true; do
        show_status
        echo ""
        echo "Refreshing in 60 seconds... (Ctrl+C to exit)"
        sleep 60
    done
else
    show_status
fi
