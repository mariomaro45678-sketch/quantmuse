#!/usr/bin/env python3
"""
Test script for Correlation Tracker

Fetches live market data and displays correlation analysis.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher
from data_service.factors.correlation_tracker import CorrelationTracker


async def main():
    print("=" * 60)
    print("CORRELATION TRACKER TEST")
    print("=" * 60)

    # Initialize components
    fetcher = HyperliquidFetcher(mode="mock")
    tracker = CorrelationTracker()

    # Symbols to test (from all strategies)
    symbols = [
        # Momentum (stocks)
        "TSLA", "NVDA", "AMD", "COIN",
        # Mean Reversion (metals)
        "XAU", "XAG",
        # Sentiment (mega-cap)
        "AAPL", "GOOGL", "MSFT", "AMZN", "META",
    ]

    print(f"\nFetching data for: {', '.join(symbols)}\n")

    # Fetch market data (need more history for correlation)
    market_data = {}
    for sym in symbols:
        try:
            df = await fetcher.get_candles(sym, timeframe='1h', limit=800)  # ~33 days
            if not df.empty:
                market_data[sym] = df
                print(f"  {sym}: {len(df)} candles loaded")
            else:
                print(f"  {sym}: No data")
        except Exception as e:
            print(f"  {sym}: Error - {e}")

    if len(market_data) < 2:
        print("\nNeed at least 2 symbols with data. Exiting.")
        return

    # Calculate correlations
    print("\n" + "=" * 60)
    print("CALCULATING CORRELATIONS...")
    print("=" * 60)

    state = tracker.calculate(market_data)

    if state.correlation_matrix.empty:
        print("\nCould not calculate correlations (insufficient data)")
        return

    # Display correlation matrix
    print("\nCORRELATION MATRIX:")
    print("-" * 60)

    # Header
    symbols_in_matrix = list(state.correlation_matrix.columns)
    header = "        " + "  ".join([f"{s:>6}" for s in symbols_in_matrix])
    print(header)

    # Rows
    for sym in symbols_in_matrix:
        row = f"{sym:>6}  "
        for sym2 in symbols_in_matrix:
            corr = state.correlation_matrix.loc[sym, sym2]
            if sym == sym2:
                row += f"{'1.00':>6}  "
            else:
                # Color code (text markers for terminal)
                if abs(corr) > 0.85:
                    row += f"{corr:>6.2f}* "  # Very high
                elif abs(corr) > 0.70:
                    row += f"{corr:>6.2f}+ "  # High
                else:
                    row += f"{corr:>6.2f}  "  # Normal
        print(row)

    print("\n* = Very high (>0.85), + = High (>0.70)")

    # High correlation pairs
    print("\n" + "=" * 60)
    print("HIGH CORRELATION PAIRS")
    print("=" * 60)

    if state.high_correlation_pairs:
        print(f"\n{'Pair':<20} {'Correlation':>12} {'Risk Note':<30}")
        print("-" * 62)
        for pair in state.high_correlation_pairs:
            risk_note = "VERY HIGH - treat as single position" if pair.correlation > 0.85 else "High - reduce combined size"
            print(f"{pair.symbol_a}/{pair.symbol_b:<15} {pair.correlation:>12.2f} {risk_note:<30}")
    else:
        print("\nNo high correlation pairs detected (all < 0.70)")

    # Portfolio metrics
    print("\n" + "=" * 60)
    print("PORTFOLIO METRICS")
    print("=" * 60)

    print(f"\n  Average Portfolio Correlation: {state.avg_portfolio_correlation:.2f}")
    print(f"  Diversification Score: {state.diversification_score:.2f}")
    print(f"    (1.0 = fully diversified, 0.0 = perfectly correlated)")

    # Effective exposure example
    print("\n" + "=" * 60)
    print("EFFECTIVE EXPOSURE EXAMPLE")
    print("=" * 60)

    # Simulate a portfolio with 20% in each of a few symbols
    example_positions = {
        "XAU": 0.20,
        "XAG": 0.20,
        "NVDA": 0.15,
        "AMD": 0.15,
    }

    print(f"\nExample portfolio:")
    for sym, pct in example_positions.items():
        print(f"  {sym}: {pct:.0%}")

    exposure = tracker.get_effective_exposure(example_positions, state)

    print(f"\n  Nominal exposure:   {exposure['nominal_exposure']:.0%}")
    print(f"  Effective exposure: {exposure['effective_exposure']:.0%}")
    print(f"  Exposure ratio:     {exposure['exposure_ratio']:.2f}x")

    if exposure['exposure_ratio'] > 1.1:
        print(f"\n  WARNING: Effective exposure is {exposure['exposure_ratio']:.0%} of nominal")
        print(f"  due to correlated positions moving together")

    # Correlation groups
    print("\n" + "=" * 60)
    print("CORRELATION GROUPS")
    print("=" * 60)

    groups = tracker.get_correlation_groups(symbols_in_matrix)
    for group_name, group_symbols in groups.items():
        print(f"\n  {group_name}: {', '.join(group_symbols)}")

    # Recommendations
    print("\n" + "=" * 60)
    print("RECOMMENDATIONS")
    print("=" * 60)

    if state.high_correlation_pairs:
        print("\n  1. For highly correlated pairs, treat combined position as single exposure")
        print("     Example: XAU + XAG at 20% each = ~37% effective exposure, not 40%")

        print("\n  2. Consider position limits:")
        for pair in state.high_correlation_pairs[:3]:
            print(f"     - {pair.symbol_a}/{pair.symbol_b}: Max combined 30% (not 30% each)")

    if state.diversification_score < 0.5:
        print("\n  3. LOW DIVERSIFICATION ALERT")
        print("     Portfolio is highly concentrated in correlated assets")
        print("     Consider adding uncorrelated assets or reducing positions")
    else:
        print("\n  3. Diversification is adequate")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
