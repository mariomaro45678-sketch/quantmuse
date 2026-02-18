#!/usr/bin/env python3
"""
Test script for Strategy Ensemble Coordinator

Tests ensemble voting logic with mock signals from multiple strategies.
"""

import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.strategies.ensemble_coordinator import (
    EnsembleCoordinator, StrategySignal, PortfolioSentiment
)


def create_mock_signal(strategy: str, symbol: str, direction: str, confidence: float) -> dict:
    """Create a mock signal dict (mimics Signal object)."""
    return {
        'direction': direction,
        'confidence': confidence,
        'generated_at': datetime.now(),
    }


def test_basic_ensemble():
    """Test basic ensemble functionality."""
    print("=" * 60)
    print("ENSEMBLE COORDINATOR TEST")
    print("=" * 60)

    coordinator = EnsembleCoordinator()

    # === TEST 1: Single strategy, no ensemble effect ===
    print("\n--- Test 1: Single Strategy ---")
    coordinator.update_signals("momentum", {
        "TSLA": create_mock_signal("momentum", "TSLA", "long", 0.75),
        "NVDA": create_mock_signal("momentum", "NVDA", "short", 0.65),
    })

    ensembled = coordinator.get_ensembled_signals()
    for sym, sig in ensembled.items():
        print(f"  {sym}: {sig.direction.upper()} @ {sig.confidence:.2f} "
              f"(original: {sig.original_confidence:.2f}) | {sig.adjustment_reason}")

    # === TEST 2: Two strategies agree ===
    print("\n--- Test 2: Two Strategies Agree (1.15x boost) ---")
    coordinator.update_signals("sentiment", {
        "TSLA": create_mock_signal("sentiment", "TSLA", "long", 0.70),
        "AAPL": create_mock_signal("sentiment", "AAPL", "long", 0.80),
    })

    ensembled = coordinator.get_ensembled_signals()
    tsla = ensembled.get("TSLA")
    if tsla:
        expected = 0.75 * 1.15  # Higher of two * boost
        print(f"  TSLA: {tsla.direction.upper()} @ {tsla.confidence:.2f} "
              f"(expected ~{expected:.2f}) | {tsla.adjustment_reason}")
        print(f"  Contributors: {tsla.contributing_strategies}")

    # === TEST 3: Three strategies agree ===
    print("\n--- Test 3: Three Strategies Agree (1.30x boost) ---")
    coordinator.update_signals("mean_reversion", {
        "TSLA": create_mock_signal("mean_reversion", "TSLA", "long", 0.60),
    })

    ensembled = coordinator.get_ensembled_signals()
    tsla = ensembled.get("TSLA")
    if tsla:
        expected = 0.75 * 1.30  # Highest of three * big boost
        print(f"  TSLA: {tsla.direction.upper()} @ {tsla.confidence:.2f} "
              f"(expected ~{min(1.0, expected):.2f}) | {tsla.adjustment_reason}")
        print(f"  Contributors: {tsla.contributing_strategies}")

    # === TEST 4: Conflict resolution ===
    print("\n--- Test 4: Conflict Resolution ---")
    coordinator.clear()
    coordinator.update_signals("momentum", {
        "BTC": create_mock_signal("momentum", "BTC", "long", 0.75),
    })
    coordinator.update_signals("sentiment", {
        "BTC": create_mock_signal("sentiment", "BTC", "short", 0.55),
    })

    ensembled = coordinator.get_ensembled_signals()
    btc = ensembled.get("BTC")
    if btc:
        print(f"  BTC: {btc.direction.upper()} @ {btc.confidence:.2f} | {btc.adjustment_reason}")
        print(f"  Conflict resolved: {btc.conflict_resolved}")

    # === TEST 5: High-confidence conflict (go flat) ===
    print("\n--- Test 5: High-Confidence Conflict (Go Flat) ---")
    coordinator.clear()
    coordinator.update_signals("momentum", {
        "ETH": create_mock_signal("momentum", "ETH", "long", 0.80),
    })
    coordinator.update_signals("sentiment", {
        "ETH": create_mock_signal("sentiment", "ETH", "short", 0.75),
    })

    ensembled = coordinator.get_ensembled_signals()
    eth = ensembled.get("ETH")
    if eth:
        print(f"  ETH: {eth.direction.upper()} @ {eth.confidence:.2f} | {eth.adjustment_reason}")
        print(f"  (Both >60% conf, should go flat)")

    # === TEST 6: Portfolio Sentiment ===
    print("\n--- Test 6: Portfolio Sentiment ---")
    coordinator.clear()
    coordinator.update_signals("momentum", {
        "TSLA": create_mock_signal("momentum", "TSLA", "long", 0.70),
        "NVDA": create_mock_signal("momentum", "NVDA", "long", 0.65),
        "AMD": create_mock_signal("momentum", "AMD", "long", 0.75),
    })
    coordinator.update_signals("sentiment", {
        "AAPL": create_mock_signal("sentiment", "AAPL", "long", 0.60),
        "GOOGL": create_mock_signal("sentiment", "GOOGL", "flat", 0.30),
    })

    sentiment = coordinator.get_portfolio_sentiment()
    print(f"  Sentiment: {sentiment.sentiment.upper()}")
    print(f"  Longs: {sentiment.long_count} | Shorts: {sentiment.short_count} | Flat: {sentiment.flat_count}")
    print(f"  Bullish %: {sentiment.bullish_pct:.0%} | Bearish %: {sentiment.bearish_pct:.0%}")
    print(f"  Avg Confidence: {sentiment.avg_confidence:.2f}")

    # === TEST 7: Should reduce exposure ===
    print("\n--- Test 7: Exposure Reduction Check ---")
    reduce, reason = coordinator.should_reduce_exposure(sentiment)
    print(f"  Should reduce: {reduce}")
    if reason:
        print(f"  Reason: {reason}")

    # Test with mixed sentiment
    coordinator.clear()
    coordinator.update_signals("momentum", {
        "TSLA": create_mock_signal("momentum", "TSLA", "long", 0.45),
        "NVDA": create_mock_signal("momentum", "NVDA", "short", 0.40),
    })
    coordinator.update_signals("sentiment", {
        "AAPL": create_mock_signal("sentiment", "AAPL", "short", 0.50),
        "GOOGL": create_mock_signal("sentiment", "GOOGL", "long", 0.35),
    })

    sentiment = coordinator.get_portfolio_sentiment()
    reduce, reason = coordinator.should_reduce_exposure(sentiment)
    print(f"\n  Mixed scenario:")
    print(f"  Sentiment: {sentiment.sentiment} | Avg conf: {sentiment.avg_confidence:.2f}")
    print(f"  Should reduce: {reduce}")
    if reason:
        print(f"  Reason: {reason}")

    # === Log full ensemble summary ===
    print("\n--- Full Ensemble Summary ---")
    coordinator.clear()
    coordinator.update_signals("momentum", {
        "TSLA": create_mock_signal("momentum", "TSLA", "long", 0.75),
        "NVDA": create_mock_signal("momentum", "NVDA", "long", 0.70),
    })
    coordinator.update_signals("sentiment", {
        "TSLA": create_mock_signal("sentiment", "TSLA", "long", 0.65),
        "AAPL": create_mock_signal("sentiment", "AAPL", "short", 0.55),
    })
    coordinator.update_signals("mean_reversion", {
        "XAU": create_mock_signal("mean_reversion", "XAU", "short", 0.60),
    })

    ensembled = coordinator.get_ensembled_signals()
    coordinator.log_ensemble_summary(ensembled)

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    test_basic_ensemble()
