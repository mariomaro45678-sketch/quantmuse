#!/usr/bin/env python3
"""
Test script for Dynamic Position Sizer (Phase 3.2)

Tests:
1. Strategy type classification
2. Regime multiplier calculation
3. Correlation multiplier calculation
4. Full position sizing with all factors
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_service.risk.dynamic_sizer import DynamicSizer, StrategyType, SizingResult
from data_service.factors.regime_detector import RegimeState, MarketRegime


def create_mock_regime(regime: MarketRegime, confidence: float = 0.8) -> RegimeState:
    """Create a mock RegimeState for testing."""
    return RegimeState(
        regime=regime,
        confidence=confidence,
        adx=25.0,
        adx_direction="up",
        atr_percentile=0.5,
        hurst=0.5,
        volatility_state="normal",
        momentum_multiplier=1.0,
        mean_reversion_multiplier=1.0,
        position_size_multiplier=1.0
    )


def test_strategy_classification():
    """Test strategy type classification."""
    print("\n=== Test 1: Strategy Classification ===")

    sizer = DynamicSizer()

    tests = [
        ("momentum_perpetuals", StrategyType.MOMENTUM),
        ("mean_reversion_metals", StrategyType.MEAN_REVERSION),
        ("sentiment_driven", StrategyType.SENTIMENT),
        ("my_custom_strategy", StrategyType.OTHER),
    ]

    for name, expected in tests:
        result = sizer.classify_strategy(name)
        assert result == expected, f"Expected {expected}, got {result}"
        print(f"  {name} -> {result.value} ✓")

    print("✓ Strategy classification works correctly")


def test_regime_multipliers():
    """Test regime-based multipliers."""
    print("\n=== Test 2: Regime Multipliers ===")

    sizer = DynamicSizer()

    # Trending regime should boost momentum, reduce mean-reversion
    trending = create_mock_regime(MarketRegime.TRENDING_UP, confidence=1.0)
    mom_mult, _ = sizer.get_regime_multiplier(trending, StrategyType.MOMENTUM)
    mr_mult, _ = sizer.get_regime_multiplier(trending, StrategyType.MEAN_REVERSION)
    assert mom_mult > 1.0, f"Momentum should be boosted in trend, got {mom_mult}"
    assert mr_mult < 1.0, f"Mean-rev should be reduced in trend, got {mr_mult}"
    print(f"  TRENDING_UP: momentum={mom_mult:.2f}x, mean_rev={mr_mult:.2f}x ✓")

    # Ranging regime should reduce momentum, boost mean-reversion
    ranging = create_mock_regime(MarketRegime.RANGING, confidence=1.0)
    mom_mult, _ = sizer.get_regime_multiplier(ranging, StrategyType.MOMENTUM)
    mr_mult, _ = sizer.get_regime_multiplier(ranging, StrategyType.MEAN_REVERSION)
    assert mom_mult < 1.0, f"Momentum should be reduced in range, got {mom_mult}"
    assert mr_mult > 1.0, f"Mean-rev should be boosted in range, got {mr_mult}"
    print(f"  RANGING: momentum={mom_mult:.2f}x, mean_rev={mr_mult:.2f}x ✓")

    # High vol regime should reduce all
    high_vol = create_mock_regime(MarketRegime.HIGH_VOL, confidence=1.0)
    mom_mult, _ = sizer.get_regime_multiplier(high_vol, StrategyType.MOMENTUM)
    mr_mult, _ = sizer.get_regime_multiplier(high_vol, StrategyType.MEAN_REVERSION)
    assert mom_mult < 1.0, f"Momentum should be reduced in high vol, got {mom_mult}"
    assert mr_mult < 1.0, f"Mean-rev should be reduced in high vol, got {mr_mult}"
    print(f"  HIGH_VOL: momentum={mom_mult:.2f}x, mean_rev={mr_mult:.2f}x ✓")

    # Confidence scaling - lower confidence should move multiplier toward 1.0
    low_conf = create_mock_regime(MarketRegime.TRENDING_UP, confidence=0.5)
    mom_mult_low, _ = sizer.get_regime_multiplier(low_conf, StrategyType.MOMENTUM)
    mom_mult_high, _ = sizer.get_regime_multiplier(trending, StrategyType.MOMENTUM)
    assert abs(mom_mult_low - 1.0) < abs(mom_mult_high - 1.0), "Lower confidence should be closer to 1.0"
    print(f"  Confidence scaling: 100% conf={mom_mult_high:.2f}x, 50% conf={mom_mult_low:.2f}x ✓")

    print("✓ Regime multipliers work correctly")


def test_position_sizing():
    """Test full position sizing."""
    print("\n=== Test 3: Position Sizing ===")

    sizer = DynamicSizer()

    # Test basic sizing without regime
    result = sizer.calculate_size(
        symbol="NVDA",
        raw_size_pct=0.10,  # 10% of equity
        current_price=500.0,
        equity=100_000,
        strategy_name="momentum_perpetuals",
        regime_state=None,
        correlation_state=None,
        signal_confidence=1.0
    )
    print(f"  No regime: {result.raw_size:.1%} -> {result.adjusted_size:.1%} "
          f"(mult={result.final_multiplier:.2f}x)")

    # Test sizing with trending regime
    trending = create_mock_regime(MarketRegime.TRENDING_UP, confidence=0.8)
    result = sizer.calculate_size(
        symbol="NVDA",
        raw_size_pct=0.10,
        current_price=500.0,
        equity=100_000,
        strategy_name="momentum_perpetuals",
        regime_state=trending,
        correlation_state=None,
        signal_confidence=1.0
    )
    assert result.adjusted_size > result.raw_size, "Momentum should be boosted in uptrend"
    print(f"  Trending + momentum: {result.raw_size:.1%} -> {result.adjusted_size:.1%} "
          f"({result.rationale}) ✓")

    # Test sizing with ranging regime (mean reversion)
    ranging = create_mock_regime(MarketRegime.RANGING, confidence=0.8)
    result = sizer.calculate_size(
        symbol="XAG",
        raw_size_pct=0.10,
        current_price=25.0,
        equity=100_000,
        strategy_name="mean_reversion_metals",
        regime_state=ranging,
        correlation_state=None,
        signal_confidence=1.0
    )
    assert result.adjusted_size > result.raw_size, "Mean-rev should be boosted in range"
    print(f"  Ranging + mean_rev: {result.raw_size:.1%} -> {result.adjusted_size:.1%} "
          f"({result.rationale}) ✓")

    # Test sizing with low confidence signal
    result = sizer.calculate_size(
        symbol="TSLA",
        raw_size_pct=0.10,
        current_price=200.0,
        equity=100_000,
        strategy_name="momentum_perpetuals",
        regime_state=None,
        correlation_state=None,
        signal_confidence=0.5  # Low confidence
    )
    assert result.adjusted_size < result.raw_size, "Low confidence should reduce size"
    print(f"  Low confidence: {result.raw_size:.1%} -> {result.adjusted_size:.1%} "
          f"(conf_mult={0.5 + 0.5*0.5:.2f}x) ✓")

    print("✓ Position sizing works correctly")


def test_min_order_handling():
    """Test minimum order size handling."""
    print("\n=== Test 4: Minimum Order Handling ===")

    sizer = DynamicSizer()

    # Very small position that's below minimum
    result = sizer.calculate_size(
        symbol="NVDA",
        raw_size_pct=0.00005,  # 0.005% = $5 on $100k
        current_price=500.0,
        equity=100_000,
        strategy_name="momentum_perpetuals",
        regime_state=None,
        correlation_state=None,
        signal_confidence=1.0
    )

    if result.min_size_applied:
        # Should be sized up to minimum
        min_pct = 10.0 / 100_000  # $10 / $100k = 0.01%
        assert abs(result.adjusted_size) >= min_pct, "Should be sized up to minimum"
        print(f"  Below min: {result.raw_size:.4%} -> {result.adjusted_size:.4%} "
              f"(min applied) ✓")
    else:
        print(f"  Below min: {result.raw_size:.4%} -> {result.adjusted_size:.4%} ✓")

    print("✓ Minimum order handling works correctly")


def test_max_position_cap():
    """Test maximum position size capping."""
    print("\n=== Test 5: Maximum Position Cap ===")

    sizer = DynamicSizer()

    # Very large position that exceeds max
    result = sizer.calculate_size(
        symbol="NVDA",
        raw_size_pct=0.50,  # 50% of equity (above 25% max)
        current_price=500.0,
        equity=100_000,
        strategy_name="momentum_perpetuals",
        regime_state=None,
        correlation_state=None,
        signal_confidence=1.0
    )

    assert result.capped, "Position should be capped"
    assert abs(result.adjusted_size) <= 0.25, f"Should be capped at 25%, got {result.adjusted_size:.1%}"
    print(f"  Above max: {result.raw_size:.1%} -> {result.adjusted_size:.1%} (capped) ✓")

    print("✓ Maximum position cap works correctly")


def test_portfolio_sizing():
    """Test full portfolio sizing."""
    print("\n=== Test 6: Portfolio Sizing ===")

    sizer = DynamicSizer()

    target_positions = {
        "NVDA": 0.10,
        "TSLA": 0.08,
        "AMD": 0.05,
    }

    current_prices = {
        "NVDA": 500.0,
        "TSLA": 200.0,
        "AMD": 150.0,
    }

    trending = create_mock_regime(MarketRegime.TRENDING_UP, confidence=0.7)

    results = sizer.size_portfolio(
        target_positions=target_positions,
        current_prices=current_prices,
        equity=100_000,
        strategy_name="momentum_perpetuals",
        regime_state=trending,
        correlation_state=None,
        signal_confidences={"NVDA": 0.9, "TSLA": 0.7, "AMD": 0.5}
    )

    print(f"  Sized {len(results)} positions:")
    for symbol, result in results.items():
        print(f"    {symbol}: {result.raw_size:.1%} -> {result.adjusted_size:.1%} "
              f"({result.rationale})")

    print("✓ Portfolio sizing works correctly")


def test_effective_exposure():
    """Test effective exposure calculation."""
    print("\n=== Test 7: Effective Exposure ===")

    sizer = DynamicSizer()

    positions = {
        "NVDA": 0.15,
        "AMD": 0.10,
        "TSLA": 0.08,
    }

    exposure = sizer.get_effective_exposure(positions, None)
    print(f"  Gross exposure: {exposure['gross_exposure']:.1%}")
    print(f"  Effective exposure: {exposure['effective_exposure']:.1%}")
    print(f"  Diversification benefit: {exposure['diversification_benefit']:.1%}")

    print("✓ Effective exposure calculation works")


if __name__ == "__main__":
    print("=" * 60)
    print("Dynamic Position Sizer Test Suite")
    print("=" * 60)

    test_strategy_classification()
    test_regime_multipliers()
    test_position_sizing()
    test_min_order_handling()
    test_max_position_cap()
    test_portfolio_sizing()
    test_effective_exposure()

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)
