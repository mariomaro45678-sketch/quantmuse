#!/usr/bin/env python3
"""
Test script for Adaptive Parameter Tuning (Phase 5.1)

Tests:
1. Parameter specification and bounds
2. Parameter set hashing
3. Recording trade parameters
4. Computing rolling performance
5. Adaptation logic with weekly limits
6. Database persistence
"""

import sys
import os
import tempfile
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_service.strategies.parameter_adapter import (
    ParameterAdapter, ParameterSpec, ParameterSet, ParameterType,
    STRATEGY_PARAMETERS
)


def test_parameter_spec():
    """Test parameter specification bounds and clamping."""
    print("\n=== Test 1: Parameter Specification ===")

    spec = ParameterSpec(
        name="rsi_oversold",
        param_type=ParameterType.THRESHOLD,
        default_value=30,
        min_value=20,
        max_value=40,
        step_size=1
    )

    # Test clamping
    assert spec.clamp(15) == 20, "Should clamp to min"
    assert spec.clamp(50) == 40, "Should clamp to max"
    assert spec.clamp(35) == 35, "Should keep valid value"
    print(f"  Clamping: 15->{spec.clamp(15)}, 50->{spec.clamp(50)}, 35->{spec.clamp(35)}")

    # Test step rounding
    assert spec.round_to_step(30.4) == 30, "Should round down"
    assert spec.round_to_step(30.6) == 31, "Should round up"
    print(f"  Rounding: 30.4->{spec.round_to_step(30.4)}, 30.6->{spec.round_to_step(30.6)}")

    print("  PASSED")


def test_parameter_set_hashing():
    """Test parameter set creation and hashing."""
    print("\n=== Test 2: Parameter Set Hashing ===")

    params1 = {"rsi_oversold": 30, "rsi_overbought": 70}
    params2 = {"rsi_overbought": 70, "rsi_oversold": 30}  # Same params, different order
    params3 = {"rsi_oversold": 31, "rsi_overbought": 70}  # Different value

    set1 = ParameterSet("test_strategy", params1)
    set2 = ParameterSet("test_strategy", params2)
    set3 = ParameterSet("test_strategy", params3)

    assert set1.hash == set2.hash, "Same params should have same hash"
    assert set1.hash != set3.hash, "Different params should have different hash"
    print(f"  Set1 hash: {set1.hash}")
    print(f"  Set2 hash (same): {set2.hash}")
    print(f"  Set3 hash (different): {set3.hash}")

    # Test JSON round-trip
    json_str = set1.to_json()
    set_restored = ParameterSet.from_json("test_strategy", json_str)
    assert set_restored.hash == set1.hash, "JSON round-trip should preserve hash"
    print("  JSON round-trip preserved hash")

    print("  PASSED")


def test_parameter_adapter_init():
    """Test adapter initialization with temp database."""
    print("\n=== Test 3: Adapter Initialization ===")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        adapter = ParameterAdapter(db_path=db_path)

        # Check all strategies have default parameters
        for strategy_name in STRATEGY_PARAMETERS.keys():
            params = adapter.get_parameters(strategy_name)
            assert params, f"Should have params for {strategy_name}"
            print(f"  {strategy_name}: {len(params)} parameters loaded")

        # Check tables were created
        with sqlite3.connect(db_path) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables]

            assert "parameter_snapshots" in table_names
            assert "parameter_performance" in table_names
            assert "parameter_adjustments" in table_names
            assert "active_parameters" in table_names
            print(f"  Created tables: {', '.join(table_names)}")

        print("  PASSED")
    finally:
        os.unlink(db_path)


def test_record_trade_parameters():
    """Test recording parameters at trade time."""
    print("\n=== Test 4: Recording Trade Parameters ===")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        adapter = ParameterAdapter(db_path=db_path)

        # Record some trades
        for i in range(5):
            adapter.record_trade_parameters(
                trade_id=1000 + i,
                strategy_name="momentum_perpetuals",
                timestamp=datetime.now() - timedelta(hours=i)
            )

        # Verify records
        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM parameter_snapshots WHERE strategy_name = ?",
                ("momentum_perpetuals",)
            ).fetchone()[0]

        assert count == 5, f"Expected 5 snapshots, got {count}"
        print(f"  Recorded {count} parameter snapshots")

        print("  PASSED")
    finally:
        os.unlink(db_path)


def test_adaptation_constraints():
    """Test adaptation respects weekly limits."""
    print("\n=== Test 5: Adaptation Constraints ===")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        adapter = ParameterAdapter(db_path=db_path)
        strategy = "momentum_perpetuals"

        # Create trades table and insert test data
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY,
                    order_id INTEGER UNIQUE,
                    symbol TEXT,
                    side TEXT,
                    strategy_name TEXT,
                    created_at TIMESTAMP,
                    realized_pnl REAL
                )
            """)

            # Insert trades with current params (moderate performance)
            for i in range(30):
                conn.execute(
                    """INSERT INTO trades (order_id, symbol, side, strategy_name,
                       created_at, realized_pnl) VALUES (?, ?, ?, ?, ?, ?)""",
                    (i, "NVDA", "buy", strategy,
                     (datetime.now() - timedelta(days=i % 30)).isoformat(),
                     1.0 if i % 3 != 0 else -0.5)
                )
            conn.commit()

        # Record parameter snapshots
        for i in range(30):
            adapter.record_trade_parameters(i, strategy)

        # Try adaptation (should require force since < 7 days)
        results = adapter.adapt_parameters(strategy, force=False)
        print(f"  Without force: {len(results)} changes")

        results = adapter.adapt_parameters(strategy, force=True)
        print(f"  With force: {len(results)} changes")

        # Check changes respect 10% limit
        for r in results:
            assert abs(r.change_pct) <= 0.10 + 0.001, \
                f"Change {r.change_pct:.1%} exceeds 10% limit"
            print(f"    {r.parameter_name}: {r.old_value:.4f} -> {r.new_value:.4f} "
                  f"({r.change_pct:+.1%})")

        print("  PASSED")
    finally:
        os.unlink(db_path)


def test_get_summary():
    """Test getting parameter summary."""
    print("\n=== Test 6: Get Summary ===")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        adapter = ParameterAdapter(db_path=db_path)
        summary = adapter.get_summary()

        assert "momentum_perpetuals" in summary
        assert "mean_reversion_metals" in summary
        assert "sentiment_driven" in summary

        for strategy, data in summary.items():
            print(f"  {strategy}:")
            print(f"    Parameters: {len(data['parameters'])} defined")
            print(f"    Trades 30d: {data['trades_30d']}")

        print("  PASSED")
    finally:
        os.unlink(db_path)


def test_strategy_parameters_complete():
    """Test all expected parameters are defined."""
    print("\n=== Test 7: Strategy Parameters Complete ===")

    expected = {
        "momentum_perpetuals": ["funding_threshold", "adx_threshold", "volume_min_threshold", "cooldown_minutes"],
        "mean_reversion_metals": ["rsi_oversold", "rsi_overbought", "bb_period", "bb_std", "ratio_zscore_threshold"],
        "sentiment_driven": ["momentum_threshold", "volume_min", "expiry_hours"],
    }

    for strategy, params in expected.items():
        assert strategy in STRATEGY_PARAMETERS, f"Missing strategy: {strategy}"
        for param in params:
            assert param in STRATEGY_PARAMETERS[strategy], f"Missing param: {strategy}.{param}"
        print(f"  {strategy}: {len(params)} params defined")

    print("  PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("Adaptive Parameter Tuning Test Suite")
    print("=" * 60)

    test_parameter_spec()
    test_parameter_set_hashing()
    test_parameter_adapter_init()
    test_record_trade_parameters()
    test_adaptation_constraints()
    test_get_summary()
    test_strategy_parameters_complete()

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)
