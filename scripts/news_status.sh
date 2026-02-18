#!/bin/bash
# Quick status check for news collector and related services
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
HEALTH_FILE="$PROJECT_DIR/logs/news_collector_health.json"

echo "========================================"
echo "News Collector Status"
echo "$(date)"
echo "========================================"
echo ""

# Check for running processes
echo "PROCESSES"
echo "----------------------------------------"

# News collector
COLLECTOR_PID=$(pgrep -f "news_collector.py" 2>/dev/null | head -1)
if [ -n "$COLLECTOR_PID" ]; then
    ELAPSED=$(ps -p $COLLECTOR_PID -o etime= 2>/dev/null | xargs)
    echo "✅ Collector:  Running (PID $COLLECTOR_PID, uptime: $ELAPSED)"
else
    echo "❌ Collector:  NOT RUNNING"
fi

# Watchdog
WATCHDOG_PID=$(pgrep -f "watchdog.py --daemon" 2>/dev/null | head -1)
if [ -n "$WATCHDOG_PID" ]; then
    ELAPSED=$(ps -p $WATCHDOG_PID -o etime= 2>/dev/null | xargs)
    echo "✅ Watchdog:   Running (PID $WATCHDOG_PID, uptime: $ELAPSED)"
else
    echo "⚠️  Watchdog:   NOT RUNNING (optional)"
fi

echo ""

# Check health file
echo "HEALTH STATUS"
echo "----------------------------------------"

if [ -f "$HEALTH_FILE" ]; then
    # Parse health file with Python
    python3 - "$HEALTH_FILE" << 'PYEOF'
import json
import sys
from datetime import datetime

try:
    with open(sys.argv[1]) as f:
        h = json.load(f)

    status = h.get('status', 'unknown')
    icon = '✅' if status == 'healthy' else '⚠️' if status in ('starting', 'stopped') else '❌'
    print(f"{icon} Status:      {status}")

    cycles = h.get('cycles', 0)
    articles = h.get('articles_processed', 0)
    print(f"   Cycles:      {cycles}")
    print(f"   Articles:    {articles}")

    # Uptime
    uptime = h.get('uptime_seconds', 0)
    hours = int(uptime // 3600)
    mins = int((uptime % 3600) // 60)
    print(f"   Uptime:      {hours}h {mins}m")

    # Last update
    ts = h.get('timestamp')
    if ts:
        ts_dt = datetime.fromisoformat(ts)
        age = (datetime.now() - ts_dt).total_seconds()
        print(f"   Last update: {int(age)}s ago")

    # Failures
    failures = h.get('consecutive_failures', 0)
    if failures > 0:
        print(f"   ⚠️  Failures:  {failures}")

    # Source stats
    src = h.get('source_stats', {})
    if src:
        print("   Sources:")
        for name, data in src.items():
            fetched = data.get('fetched', 0)
            failed = data.get('failures', 0)
            icon = '✅' if failed == 0 else '⚠️'
            print(f"     {icon} {name}: {fetched} fetched, {failed} failures")

    # Error
    error = h.get('error')
    if error:
        print(f"   ❌ Error:     {error}")

except Exception as e:
    print(f"❌ Error reading health file: {e}")
PYEOF
else
    echo "⚠️  Health file not found"
    echo "   Expected: $HEALTH_FILE"
fi

echo ""

# Check recent logs
echo "RECENT ACTIVITY"
echo "----------------------------------------"
LATEST_LOG=$(ls -t "$PROJECT_DIR/logs/news_collector_"*.log 2>/dev/null | head -1)
if [ -n "$LATEST_LOG" ] && [ -f "$LATEST_LOG" ]; then
    echo "Latest log: $(basename "$LATEST_LOG")"
    echo ""
    echo "Last 5 lines:"
    tail -5 "$LATEST_LOG" | sed 's/^/  /'
else
    echo "No collector logs found"
fi

echo ""
echo "========================================"

# Commands
echo ""
echo "USEFUL COMMANDS"
echo "  Start:   ./scripts/start_news_service.sh"
echo "  Stop:    ./scripts/stop_news_service.sh"
echo "  Logs:    tail -f logs/news_collector_*.log"
echo "  Health:  cat logs/news_collector_health.json | python3 -m json.tool"
echo ""
