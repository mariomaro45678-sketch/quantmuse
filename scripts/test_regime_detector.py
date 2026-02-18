#!/usr/bin/env python3
"""
Test script for Market Regime Detector

Fetches live market data and displays current regime classification.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher
from data_service.factors.regime_detector import RegimeDetector, MarketRegime


async def main():
    print("=" * 60)
    print("MARKET REGIME DETECTOR TEST")
    print("=" * 60)

    # Initialize components
    fetcher = HyperliquidFetcher(mode="mock")
    detector = RegimeDetector()

    # Symbols to test (from all strategies)
    symbols = [
        # Momentum (stocks)
        "TSLA", "NVDA", "AMD", "COIN",
        # Mean Reversion (metals)
        "XAU", "XAG",
        # Sentiment (mega-cap)
        "AAPL", "GOOGL", "MSFT",
    ]

    print(f"\nFetching data for: {', '.join(symbols)}\n")

    # Fetch market data
    market_data = {}
    for sym in symbols:
        try:
            df = await fetcher.get_candles(sym, timeframe='1h', limit=150)
            if not df.empty:
                market_data[sym] = df
                print(f"  {sym}: {len(df)} candles loaded")
            else:
                print(f"  {sym}: No data")
        except Exception as e:
            print(f"  {sym}: Error - {e}")

    if not market_data:
        print("\nNo market data available. Exiting.")
        return

    # Detect regime for each symbol
    print("\n" + "=" * 60)
    print("PER-SYMBOL REGIME DETECTION")
    print("=" * 60)
    print(f"{'Symbol':<8} {'Regime':<15} {'Conf':>5} {'ADX':>6} {'Hurst':>6} {'ATR%':>6} {'Vol':>8} {'Mom*':>5} {'MR*':>5}")
    print("-" * 75)

    regimes = detector.get_regime_summary(market_data)

    for sym, state in regimes.items():
        print(f"{sym:<8} {state.regime.value:<15} {state.confidence:>5.2f} "
              f"{state.adx:>6.1f} {state.hurst:>6.2f} {state.atr_percentile:>5.0%} "
              f"{state.volatility_state:>8} {state.momentum_multiplier:>5.2f} "
              f"{state.mean_reversion_multiplier:>5.2f}")

    # Portfolio-level regime
    print("\n" + "=" * 60)
    print("PORTFOLIO-LEVEL REGIME")
    print("=" * 60)

    portfolio_regime = detector.get_portfolio_regime(market_data)
    print(f"\n  Regime:     {portfolio_regime.regime.value}")
    print(f"  Confidence: {portfolio_regime.confidence:.2f}")
    print(f"  Avg ADX:    {portfolio_regime.adx:.1f}")
    print(f"  Avg Hurst:  {portfolio_regime.hurst:.2f}")
    print(f"  ATR %ile:   {portfolio_regime.atr_percentile:.0%}")
    print(f"  Volatility: {portfolio_regime.volatility_state}")
    print(f"\n  Strategy Multipliers:")
    print(f"    Momentum:       {portfolio_regime.momentum_multiplier:.2f}x")
    print(f"    Mean Reversion: {portfolio_regime.mean_reversion_multiplier:.2f}x")
    print(f"    Position Size:  {portfolio_regime.position_size_multiplier:.2f}x")

    # Interpretation
    print("\n" + "=" * 60)
    print("INTERPRETATION")
    print("=" * 60)

    r = portfolio_regime.regime
    if r == MarketRegime.TRENDING_UP:
        print("\n  BULLISH TREND DETECTED")
        print("  -> Momentum strategies should perform well")
        print("  -> Mean reversion may struggle (don't fade the trend)")
    elif r == MarketRegime.TRENDING_DOWN:
        print("\n  BEARISH TREND DETECTED")
        print("  -> Momentum strategies (short) should perform well")
        print("  -> Mean reversion may struggle")
    elif r == MarketRegime.RANGING:
        print("\n  RANGING/CHOPPY MARKET")
        print("  -> Mean reversion strategies should perform well")
        print("  -> Momentum strategies may get whipsawed")
    elif r == MarketRegime.HIGH_VOL:
        print("\n  HIGH VOLATILITY ENVIRONMENT")
        print("  -> Reduce position sizes across all strategies")
        print("  -> Wider stops, smaller bets")
    elif r == MarketRegime.LOW_VOL:
        print("\n  LOW VOLATILITY (COMPRESSION)")
        print("  -> Potential breakout setup")
        print("  -> Mean reversion slightly favored until breakout")
    else:
        print("\n  REGIME UNCLEAR")
        print("  -> Use default strategy weights")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
