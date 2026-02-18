#!/bin/bash
# Start News Collector with Watchdog
#
# This script starts both the news collector and the watchdog
# to ensure continuous operation with auto-recovery.
#
# Usage:
#   ./scripts/start_news_service.sh [--symbols SYMBOLS] [--interval MINUTES]
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOGS_DIR="$PROJECT_DIR/logs"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python3"

# Default settings
SYMBOLS="XAU,XAG,TSLA,NVDA,AMD,COIN,AAPL,GOOGL,MSFT,AMZN,META"
INTERVAL=5

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --symbols)
            SYMBOLS="$2"
            shift 2
            ;;
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Create logs directory
mkdir -p "$LOGS_DIR"

# Timestamp for log files
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "========================================"
echo "Starting News Collection Service"
echo "========================================"
echo "Symbols:  $SYMBOLS"
echo "Interval: ${INTERVAL} minutes"
echo "Time:     $(date)"
echo ""

# Stop any existing collector/watchdog
echo "Stopping existing processes..."
pkill -f "news_collector.py" 2>/dev/null || true
pkill -f "watchdog.py --daemon" 2>/dev/null || true
sleep 2

# Start the news collector
echo "Starting news collector..."
COLLECTOR_LOG="$LOGS_DIR/news_collector_${TIMESTAMP}.log"
nohup "$VENV_PYTHON" "$SCRIPT_DIR/news_collector.py" \
    --symbols "$SYMBOLS" \
    --interval "$INTERVAL" \
    > "$COLLECTOR_LOG" 2>&1 &

COLLECTOR_PID=$!
echo "$COLLECTOR_PID" > "$LOGS_DIR/news_collector.pid"
echo "  Collector PID: $COLLECTOR_PID"
echo "  Log: $COLLECTOR_LOG"

# Give collector time to start
sleep 3

# Check if collector started successfully
if ps -p $COLLECTOR_PID > /dev/null 2>&1; then
    echo "  Collector started successfully"
else
    echo "  ERROR: Collector failed to start!"
    echo "  Check log: $COLLECTOR_LOG"
    exit 1
fi

# Start the watchdog
echo ""
echo "Starting watchdog..."
WATCHDOG_LOG="$LOGS_DIR/watchdog_${TIMESTAMP}.log"
nohup "$VENV_PYTHON" "$SCRIPT_DIR/watchdog.py" \
    --daemon \
    --check-interval 60 \
    --max-stale 600 \
    --symbols "$SYMBOLS" \
    --interval "$INTERVAL" \
    > "$WATCHDOG_LOG" 2>&1 &

WATCHDOG_PID=$!
echo "$WATCHDOG_PID" > "$LOGS_DIR/watchdog.pid"
echo "  Watchdog PID: $WATCHDOG_PID"
echo "  Log: $WATCHDOG_LOG"

sleep 2

# Check if watchdog started successfully
if ps -p $WATCHDOG_PID > /dev/null 2>&1; then
    echo "  Watchdog started successfully"
else
    echo "  ERROR: Watchdog failed to start!"
    echo "  Check log: $WATCHDOG_LOG"
fi

echo ""
echo "========================================"
echo "Service Started Successfully"
echo "========================================"
echo ""
echo "Monitor commands:"
echo "  tail -f $COLLECTOR_LOG"
echo "  tail -f $WATCHDOG_LOG"
echo ""
echo "Status commands:"
echo "  $VENV_PYTHON $SCRIPT_DIR/watchdog.py --status"
echo "  ps aux | grep -E 'news_collector|watchdog'"
echo ""
echo "Stop commands:"
echo "  pkill -f news_collector.py"
echo "  pkill -f 'watchdog.py --daemon'"
echo ""
