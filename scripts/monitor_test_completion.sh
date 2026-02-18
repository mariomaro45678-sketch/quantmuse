#!/bin/bash
# Monitor test completion and auto-analyze

PID=2205704
echo "Monitoring test completion (PID: $PID)"
echo "Started at: $(date)"
echo ""

# Calculate expected completion time
START_TIME="2026-02-06 10:08:00"
DURATION_HOURS=24
echo "Test started: $START_TIME"
echo "Expected completion: ~10:08 CET (Feb 7)"
echo ""

# Wait for process to complete
echo "Waiting for test to complete..."
while ps -p $PID > /dev/null 2>&1; do
    ELAPSED=$(ps -p $PID -o etime= | xargs)
    echo -ne "\rElapsed: $ELAPSED | Status: Running   "
    sleep 10
done

echo ""
echo ""
echo "=============================================================================="
echo "TEST COMPLETED at $(date)"
echo "=============================================================================="
echo ""

# Wait a moment for final log writes
sleep 2

# Run analysis
echo "Running analysis..."
echo ""
./scripts/analyze_test_results.sh

echo ""
echo "=============================================================================="
echo "Next Steps:"
echo "=============================================================================="
echo "1. Review TEST_FINDINGS.md for detailed analysis"
echo "2. Check final summary in logs/prod_24h.log"
echo "3. Decide: Fix news collector and re-run, or proceed with 2 strategies?"
echo ""
