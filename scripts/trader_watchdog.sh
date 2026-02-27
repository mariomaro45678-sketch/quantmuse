#!/bin/bash
# ============================================================
# Trader Watchdog — monitors run_multi_strategy.py and restarts
# if it dies. Install via cron:
#   */5 * * * * /home/pap/Desktop/QuantMuse/scripts/trader_watchdog.sh >> /home/pap/Desktop/QuantMuse/logs/trader_watchdog.log 2>&1
# ============================================================

PROJECT_ROOT="/home/pap/Desktop/QuantMuse"
PYTHON="$PROJECT_ROOT/venv/bin/python3"
SCRIPT="$PROJECT_ROOT/scripts/run_multi_strategy.py"
LOGS_DIR="$PROJECT_ROOT/logs"
WATCHDOG_PID_FILE="$LOGS_DIR/trader_watchdog.pid"

# Ensure logs dir exists
mkdir -p "$LOGS_DIR"

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Check if the trader process is running
if pgrep -f "run_multi_strategy.py" > /dev/null 2>&1; then
    echo "[$TIMESTAMP] ✅ Trader is running (PID: $(pgrep -f 'run_multi_strategy.py' | head -1))"
    exit 0
fi

# Trader is NOT running — restart it
echo "[$TIMESTAMP] ⚠️  Trader is NOT running — restarting..."

LOG_FILE="$LOGS_DIR/prod_$(date '+%Y%m%d_%H%M').log"

cd "$PROJECT_ROOT"
nohup "$PYTHON" "$SCRIPT" >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$WATCHDOG_PID_FILE"

# Wait a moment and verify it started
sleep 3

if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "[$TIMESTAMP] 🚀 Trader restarted successfully (PID: $NEW_PID, log: $LOG_FILE)"
else
    echo "[$TIMESTAMP] ❌ Trader failed to start! Check $LOG_FILE"
    exit 1
fi
