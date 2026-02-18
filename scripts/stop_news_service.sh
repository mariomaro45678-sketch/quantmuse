#!/bin/bash
# Stop News Collector and Watchdog
#

echo "Stopping News Collection Service..."

# Stop watchdog first (so it doesn't restart collector)
if pkill -f "watchdog.py --daemon" 2>/dev/null; then
    echo "  Watchdog stopped"
else
    echo "  Watchdog was not running"
fi

# Stop collector
if pkill -f "news_collector.py" 2>/dev/null; then
    echo "  Collector stopped"
else
    echo "  Collector was not running"
fi

# Clean up PID files
rm -f logs/news_collector.pid logs/watchdog.pid 2>/dev/null

echo "Done."
