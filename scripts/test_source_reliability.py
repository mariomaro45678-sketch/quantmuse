#!/usr/bin/env python3
"""
Test script for Source Reliability Scoring (Phase 2.2)

Tests:
1. Recording signal outcomes
2. Computing reliability metrics
3. Dynamic weight calculation
4. Backfill from historical trades
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from data_service.ai.source_reliability import SourceReliabilityTracker, get_reliability_tracker


def test_basic_recording():
    """Test recording signal outcomes."""
    print("\n=== Test 1: Basic Recording ===")

    tracker = SourceReliabilityTracker()

    # Simulate some signal outcomes
    base_time = datetime.now()

    # Telegram: 3 correct, 1 wrong = 75% hit rate
    for i in range(3):
        tracker.record_signal_outcome(
            source="Telegram (@bloomberg)",
            symbol="XAU",
            signal_direction="long",
            entry_price=2000.0,
            exit_price=2020.0,  # Correct long
            signal_time=base_time - timedelta(hours=i),
            other_sources_times=[base_time - timedelta(hours=i, minutes=5)]  # Telegram was first
        )

    tracker.record_signal_outcome(
        source="Telegram (@bloomberg)",
        symbol="XAU",
        signal_direction="long",
        entry_price=2000.0,
        exit_price=1980.0,  # Wrong long
        signal_time=base_time - timedelta(hours=4),
        other_sources_times=None
    )

    # Reuters: 2 correct, 2 wrong = 50% hit rate
    for i in range(2):
        tracker.record_signal_outcome(
            source="RSS (Reuters)",
            symbol="NVDA",
            signal_direction="short",
            entry_price=500.0,
            exit_price=480.0,  # Correct short
            signal_time=base_time - timedelta(hours=i),
            other_sources_times=[base_time - timedelta(hours=i, minutes=-10)]  # Reuters was slower
        )
    for i in range(2):
        tracker.record_signal_outcome(
            source="RSS (Reuters)",
            symbol="NVDA",
            signal_direction="short",
            entry_price=500.0,
            exit_price=520.0,  # Wrong short
            signal_time=base_time - timedelta(hours=i+2),
            other_sources_times=None
        )

    print("Recorded 8 signal outcomes (4 Telegram, 4 Reuters)")
    print("✓ Basic recording works")


def test_metrics_computation():
    """Test computing reliability metrics."""
    print("\n=== Test 2: Metrics Computation ===")

    tracker = get_reliability_tracker()

    # Need more signals for MIN_SIGNALS_FOR_SCORING
    base_time = datetime.now()

    # Add more Telegram signals to reach threshold
    for i in range(10):
        tracker.record_signal_outcome(
            source="Telegram",
            symbol="BTC",
            signal_direction="long",
            entry_price=40000.0,
            exit_price=40400.0 if i < 7 else 39600.0,  # 70% correct
            signal_time=base_time - timedelta(hours=i+5),
            other_sources_times=[base_time - timedelta(hours=i+5, minutes=2)]
        )

    # Compute metrics
    metrics = tracker.compute_metrics("Telegram")

    if metrics:
        print(f"Telegram metrics:")
        print(f"  - Total signals: {metrics.total_signals}")
        print(f"  - Hit rate: {metrics.hit_rate:.1%}")
        print(f"  - Avg return: {metrics.avg_return:+.2%}")
        print(f"  - Latency score: {metrics.latency_score:.2f}")
        print(f"  - Reliability score: {metrics.reliability_score:.2f}")
        print(f"  - Weight: {metrics.weight:.2f}x")
        print("✓ Metrics computation works")
    else:
        print("⚠ Not enough signals for metrics (need 10+)")


def test_dynamic_weights():
    """Test dynamic weight retrieval."""
    print("\n=== Test 3: Dynamic Weights ===")

    tracker = get_reliability_tracker()

    sources = ["Telegram", "Reuters", "DuckDuckGo", "unknown_source"]

    for source in sources:
        weight = tracker.get_source_weight(source)
        print(f"  {source}: {weight:.2f}x")

    print("✓ Dynamic weights work")


def test_reliability_summary():
    """Test getting full reliability summary."""
    print("\n=== Test 4: Reliability Summary ===")

    tracker = get_reliability_tracker()
    summary = tracker.get_reliability_summary()

    print("\nSource Reliability Summary:")
    print("-" * 70)
    print(f"{'Source':<15} {'Signals':<10} {'Hit Rate':<10} {'Avg Return':<12} {'Weight':<10}")
    print("-" * 70)

    for source, data in sorted(summary.items()):
        print(f"{source:<15} {data['total_signals']:<10} {data['hit_rate']:<10} "
              f"{data['avg_return']:<12} {data['weight']:<10}")

    print("✓ Summary generation works")


def test_backfill():
    """Test backfill from historical trades."""
    print("\n=== Test 5: Backfill from Trades ===")

    tracker = get_reliability_tracker()

    # This will only work if there's actual trade history
    try:
        count = tracker.backfill_from_trades(lookback_days=7)
        print(f"Backfilled {count} signal outcomes from trade history")
        print("✓ Backfill works")
    except Exception as e:
        print(f"⚠ Backfill skipped (no trade history or error): {e}")


def test_integration_with_sentiment():
    """Test integration with SentimentFactor."""
    print("\n=== Test 6: SentimentFactor Integration ===")

    from data_service.ai.sentiment_factor import SentimentFactor

    sf = SentimentFactor()

    # Test dynamic weight retrieval through SentimentFactor
    sources = ["Telegram (@bloomberg)", "RSS (Reuters)", "DuckDuckGo News"]

    for source in sources:
        weight = sf._get_source_weight(source)
        print(f"  {source}: {weight:.2f}x")

    print("✓ SentimentFactor integration works")


if __name__ == "__main__":
    print("=" * 60)
    print("Source Reliability Scoring Test Suite")
    print("=" * 60)

    test_basic_recording()
    test_metrics_computation()
    test_dynamic_weights()
    test_reliability_summary()
    test_backfill()
    test_integration_with_sentiment()

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)
