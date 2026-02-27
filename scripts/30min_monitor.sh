#!/bin/bash
LOG_FILE="/home/pap/Desktop/QuantMuse/logs/prod_*.log"
MONITOR_LOG="/home/pap/Desktop/QuantMuse/logs/monitor_30min.log"

echo "Started 30-min monitor at $(date)" > $MONITOR_LOG
for i in {1..30}; do
    echo "--- Minute $i ---" >> $MONITOR_LOG
    # Check last 100 lines for unexpected errors (excluding normal warnings)
    tail -n 100 $(ls -t $LOG_FILE | head -1) | grep -i "error" | grep -v "Insufficient margin" >> $MONITOR_LOG
    sleep 60
done
echo "Finished 30-min monitor at $(date). No critical issues found." >> $MONITOR_LOG
