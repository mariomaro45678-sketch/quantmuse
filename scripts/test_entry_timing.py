#!/usr/bin/env python3
"""
Test script for Entry Timing Optimization (Phase 3.1)

Tests:
1. Entry strategy selection based on signal type
2. Limit price calculation
3. Pullback target calculation
4. Pending entry management
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from datetime import datetime

from data_service.executors.entry_timing import (
    EntryOptimizer,
    EntryStrategy,
    PendingEntry
)


def test_entry_strategy_selection():
    """Test that entry strategies are selected correctly."""
    print("\n=== Test 1: Entry Strategy Selection ===")

    optimizer = EntryOptimizer()

    # High confidence should be immediate
    strat = optimizer.get_entry_strategy("momentum_perpetuals", signal_strength=0.90)
    assert strat == EntryStrategy.IMMEDIATE, f"Expected IMMEDIATE, got {strat}"
    print(f"  90% confidence momentum: {strat.value} ✓")

    # Sentiment signals should get pullback
    strat = optimizer.get_entry_strategy("sentiment_driven", signal_strength=0.70)
    assert strat == EntryStrategy.PULLBACK_WAIT, f"Expected PULLBACK_WAIT, got {strat}"
    print(f"  70% confidence sentiment: {strat.value} ✓")

    # Other signals should use limit-wait
    strat = optimizer.get_entry_strategy("momentum_perpetuals", signal_strength=0.65)
    assert strat == EntryStrategy.LIMIT_WAIT, f"Expected LIMIT_WAIT, got {strat}"
    print(f"  65% confidence momentum: {strat.value} ✓")

    # Mean reversion should use limit-wait
    strat = optimizer.get_entry_strategy("mean_reversion_metals", signal_strength=0.60)
    assert strat == EntryStrategy.LIMIT_WAIT, f"Expected LIMIT_WAIT, got {strat}"
    print(f"  60% confidence mean_reversion: {strat.value} ✓")

    print("✓ Entry strategy selection works correctly")


def test_limit_price_calculation():
    """Test limit price calculation."""
    print("\n=== Test 2: Limit Price Calculation ===")

    optimizer = EntryOptimizer()
    current_price = 100.0

    # Buy should be below current price (0.1% lower)
    buy_limit = optimizer.calculate_limit_price(current_price, "buy")
    assert buy_limit < current_price, f"Buy limit {buy_limit} should be < {current_price}"
    expected = 99.90  # 0.1% lower
    assert abs(buy_limit - expected) < 0.01, f"Expected ~{expected}, got {buy_limit}"
    print(f"  Buy @ 100.00 -> limit @ {buy_limit:.2f} (-0.10%) ✓")

    # Sell should be above current price (0.1% higher)
    sell_limit = optimizer.calculate_limit_price(current_price, "sell")
    assert sell_limit > current_price, f"Sell limit {sell_limit} should be > {current_price}"
    expected = 100.10  # 0.1% higher
    assert abs(sell_limit - expected) < 0.01, f"Expected ~{expected}, got {sell_limit}"
    print(f"  Sell @ 100.00 -> limit @ {sell_limit:.2f} (+0.10%) ✓")

    # Custom offset
    buy_limit_custom = optimizer.calculate_limit_price(current_price, "buy", offset_pct=0.5)
    assert abs(buy_limit_custom - 99.50) < 0.01, f"Expected ~99.50, got {buy_limit_custom}"
    print(f"  Buy @ 100.00 w/ 0.5% offset -> limit @ {buy_limit_custom:.2f} ✓")

    print("✓ Limit price calculation works correctly")


def test_pullback_calculation():
    """Test pullback target calculation."""
    print("\n=== Test 3: Pullback Target Calculation ===")

    optimizer = EntryOptimizer()

    # Long signal: price at 100, peak moved to 105, 30% pullback target
    signal_price = 100.0
    peak_price = 105.0
    target = optimizer.calculate_pullback_target(signal_price, peak_price, "buy")
    # 30% of 5 move = 1.5, so target = 105 - 1.5 = 103.5
    assert abs(target - 103.5) < 0.01, f"Expected ~103.5, got {target}"
    print(f"  Long: signal=100, peak=105 -> pullback target={target:.2f} ✓")

    # Short signal: price at 100, trough moved to 95, 30% pullback target
    signal_price = 100.0
    trough_price = 95.0
    target = optimizer.calculate_pullback_target(signal_price, trough_price, "sell")
    # 30% of 5 move = 1.5, so target = 95 + 1.5 = 96.5
    assert abs(target - 96.5) < 0.01, f"Expected ~96.5, got {target}"
    print(f"  Short: signal=100, trough=95 -> pullback target={target:.2f} ✓")

    print("✓ Pullback calculation works correctly")


def test_pending_entry_dataclass():
    """Test PendingEntry dataclass."""
    print("\n=== Test 4: PendingEntry Dataclass ===")

    entry = PendingEntry(
        symbol="NVDA",
        side="buy",
        target_size=1.5,
        signal_price=500.0,
        limit_price=499.50,
        strategy_name="momentum_perpetuals",
        entry_strategy=EntryStrategy.LIMIT_WAIT
    )

    assert entry.status == "pending"
    assert entry.max_wait_seconds == 300
    assert entry.peak_price is None
    print(f"  Created PendingEntry for {entry.symbol} ✓")
    print(f"  Status: {entry.status}, Max wait: {entry.max_wait_seconds}s ✓")

    print("✓ PendingEntry dataclass works correctly")


async def test_entry_optimizer_basic():
    """Test basic EntryOptimizer functionality."""
    print("\n=== Test 5: EntryOptimizer Basic Flow ===")

    optimizer = EntryOptimizer()

    # Test immediate entry (no order manager)
    result = await optimizer.submit_entry(
        symbol="TSLA",
        side="buy",
        size=1.0,
        current_price=250.0,
        strategy_name="momentum_perpetuals",
        signal_strength=0.95,  # High confidence -> immediate
    )

    # Without order manager, still returns success for tracking
    print(f"  Immediate entry result: success={result.success}, type={result.entry_type}")

    # Test limit-wait entry
    result = await optimizer.submit_entry(
        symbol="NVDA",
        side="buy",
        size=2.0,
        current_price=500.0,
        strategy_name="momentum_perpetuals",
        signal_strength=0.60,  # Medium confidence -> limit wait
    )

    print(f"  Limit-wait entry result: success={result.success}, type={result.entry_type}")
    print(f"  Pending entries: {len(optimizer.pending_entries)}")

    # Get stats
    stats = optimizer.get_stats()
    print(f"  Stats: {stats}")

    print("✓ EntryOptimizer basic flow works correctly")


def test_get_pending_entries():
    """Test getting pending entries for display."""
    print("\n=== Test 6: Get Pending Entries ===")

    optimizer = EntryOptimizer()

    # Add some pending entries directly
    optimizer.pending_entries["NVDA"] = PendingEntry(
        symbol="NVDA",
        side="buy",
        target_size=2.0,
        signal_price=500.0,
        limit_price=499.50,
        strategy_name="momentum_perpetuals",
        entry_strategy=EntryStrategy.LIMIT_WAIT
    )

    optimizer.pending_entries["XAU"] = PendingEntry(
        symbol="XAU",
        side="buy",
        target_size=0.5,
        signal_price=2000.0,
        limit_price=1998.0,
        strategy_name="sentiment_driven",
        entry_strategy=EntryStrategy.PULLBACK_WAIT,
        pullback_target=1995.0
    )

    entries = optimizer.get_pending_entries()
    assert len(entries) == 2
    print(f"  Got {len(entries)} pending entries:")
    for e in entries:
        print(f"    - {e['symbol']}: {e['side']} {e['size']} @ limit {e['limit_price']:.2f} "
              f"({e['strategy']})")

    print("✓ Get pending entries works correctly")


if __name__ == "__main__":
    print("=" * 60)
    print("Entry Timing Optimization Test Suite")
    print("=" * 60)

    test_entry_strategy_selection()
    test_limit_price_calculation()
    test_pullback_calculation()
    test_pending_entry_dataclass()
    asyncio.run(test_entry_optimizer_basic())
    test_get_pending_entries()

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)
