#!/usr/bin/env python3
"""
Test script for Order Book Imbalance Factor

Fetches live order book data and displays imbalance metrics.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher
from data_service.factors.orderbook_factors import OrderBookFactors


async def main():
    print("=" * 60)
    print("ORDER BOOK IMBALANCE FACTOR TEST")
    print("=" * 60)

    # Initialize components
    fetcher = HyperliquidFetcher(mode="mock")  # Use "live" for real data
    ob_factors = OrderBookFactors()

    # Symbols to test
    symbols = [
        # Momentum (stocks)
        "TSLA", "NVDA", "AMD", "COIN",
        # Mean Reversion (metals)
        "XAU", "XAG",
        # Sentiment (mega-cap)
        "AAPL", "GOOGL", "MSFT",
    ]

    print(f"\nFetching order books for: {', '.join(symbols)}\n")

    # Fetch order book data for each symbol
    print("=" * 75)
    print(f"{'Symbol':<8} {'Imbalance':>10} {'Pressure':>10} {'Bid Vol':>10} "
          f"{'Ask Vol':>10} {'Spread%':>8} {'Levels':>8}")
    print("-" * 75)

    imbalances = {}
    for sym in symbols:
        try:
            imb = await ob_factors.calculate(sym, fetcher)
            imbalances[sym] = imb
            print(f"{sym:<8} {imb.imbalance:>+10.3f} {imb.pressure:>10} "
                  f"{imb.bid_volume:>10.2f} {imb.ask_volume:>10.2f} "
                  f"{imb.spread_pct:>7.3f}% {imb.bid_levels}/{imb.ask_levels}".ljust(8))
        except Exception as e:
            print(f"{sym:<8} Error: {e}")

    # Test confidence adjustments
    print("\n" + "=" * 60)
    print("CONFIDENCE ADJUSTMENT TESTS")
    print("=" * 60)

    test_cases = [
        ("TSLA", "long", 0.70),
        ("TSLA", "short", 0.70),
        ("NVDA", "long", 0.80),
        ("NVDA", "short", 0.80),
        ("XAU", "long", 0.65),
        ("XAG", "short", 0.65),
    ]

    print(f"\n{'Symbol':<8} {'Direction':<8} {'Base Conf':>10} {'Adjusted':>10} {'Reason':<30}")
    print("-" * 75)

    for sym, direction, base_conf in test_cases:
        if sym in imbalances:
            imb = imbalances[sym]
            adj_conf, reason = ob_factors.adjust_confidence(base_conf, direction, imb)
            delta = adj_conf - base_conf
            delta_str = f"({delta:+.2f})" if delta != 0 else ""
            print(f"{sym:<8} {direction:<8} {base_conf:>10.2f} {adj_conf:>10.2f} {delta_str:<8} {reason:<30}")

    # Portfolio summary
    print("\n" + "=" * 60)
    print("PORTFOLIO ORDER BOOK SUMMARY")
    print("=" * 60)

    # Simulate some positions for weighting
    positions = {
        "TSLA": 0.15,
        "NVDA": 0.20,
        "XAU": 0.10,
        "XAG": 0.05,
    }

    portfolio_metrics = ob_factors.get_portfolio_imbalance(imbalances, positions)

    print(f"\n  Average Imbalance: {portfolio_metrics['avg_imbalance']:+.3f}")
    print(f"  Bullish Symbols:   {portfolio_metrics['bullish_count']}")
    print(f"  Bearish Symbols:   {portfolio_metrics['bearish_count']}")
    print(f"  Neutral Symbols:   {portfolio_metrics['neutral_count']}")
    print(f"  Average Spread:    {portfolio_metrics['avg_spread']:.3f}%")
    print(f"  Total Symbols:     {portfolio_metrics['symbols_count']}")

    # Interpretation
    print("\n" + "=" * 60)
    print("INTERPRETATION")
    print("=" * 60)

    avg_imb = portfolio_metrics['avg_imbalance']
    if avg_imb > 0.2:
        print("\n  PORTFOLIO-WIDE BULLISH PRESSURE")
        print("  -> Order books show more buying interest")
        print("  -> Long signals get confidence boost")
    elif avg_imb < -0.2:
        print("\n  PORTFOLIO-WIDE BEARISH PRESSURE")
        print("  -> Order books show more selling interest")
        print("  -> Short signals get confidence boost")
    else:
        print("\n  BALANCED ORDER BOOKS")
        print("  -> No clear directional pressure")
        print("  -> Signals adjusted per-symbol")

    # Show individual symbol details
    print("\n" + "=" * 60)
    print("DETAILED SYMBOL ANALYSIS")
    print("=" * 60)

    for sym, imb in imbalances.items():
        print(f"\n  {sym}:")
        print(f"    Imbalance:  {imb.imbalance:+.3f} ({imb.pressure})")
        print(f"    Bid Volume: {imb.bid_volume:.2f} ({imb.bid_levels} levels)")
        print(f"    Ask Volume: {imb.ask_volume:.2f} ({imb.ask_levels} levels)")
        print(f"    Spread:     {imb.spread_pct:.3f}%")

        if imb.pressure == "bullish":
            print(f"    -> Buyers dominating, longs get +10% confidence")
        elif imb.pressure == "bearish":
            print(f"    -> Sellers dominating, shorts get +10% confidence")
        else:
            print(f"    -> Balanced, no adjustment")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
