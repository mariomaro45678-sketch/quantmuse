#!/usr/bin/env python3
"""
Sentiment Validation Script - Verifies sentiment data is flowing correctly.

Checks:
1. Database has articles for tracked symbols
2. Sentiment scores are being calculated
3. Momentum values are being computed
4. Signal strength vs threshold
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.ai.sentiment_factor import SentimentFactor
from data_service.storage.database_manager import DatabaseManager
from data_service.strategies.sentiment_driven import SentimentDriven

def validate_sentiment():
    """Run complete sentiment validation."""
    print("="*80)
    print("SENTIMENT PIPELINE VALIDATION")
    print("="*80)

    db = DatabaseManager()
    sentiment_factor = SentimentFactor(db_manager=db)
    strategy = SentimentDriven()

    symbols = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'META', 'TSLA', 'NVDA', 'XAU', 'XAG']

    print("\n1. DATABASE ARTICLES CHECK")
    print("-"*80)

    total_articles = 0
    for symbol in symbols:
        articles = db.get_recent_articles(symbol, hours_back=24)
        total_articles += len(articles)

        if articles:
            latest = articles[0]
            print(f"✅ {symbol:6s} | {len(articles):3d} articles | "
                  f"Latest: {latest.published_at.strftime('%H:%M:%S')} | "
                  f"Sentiment: {latest.sentiment_score:+.3f}")
        else:
            print(f"⚠️  {symbol:6s} | No articles found")

    print(f"\nTotal articles in database (24h): {total_articles}")

    print("\n2. SENTIMENT FACTORS CHECK")
    print("-"*80)

    print(f"{'Symbol':<8} {'Level':>7} {'Momentum':>9} {'Variance':>9} {'Signal':>8} {'Threshold':>10}")
    print("-"*80)

    signals_ready = 0
    for symbol in symbols:
        factors = sentiment_factor.get_factors(symbol)

        level = factors.get('sentiment_level', 0)
        momentum = factors.get('sentiment_momentum', 0)
        variance = factors.get('sentiment_variance', 0)

        # Check if would trigger trade
        threshold = strategy.momentum_threshold
        signal = "LONG" if momentum > threshold else "SHORT" if momentum < -threshold else "FLAT"

        status = "✅" if abs(momentum) > threshold else "⏳"

        print(f"{status} {symbol:<6} {level:>7.3f} {momentum:>9.3f} {variance:>9.3f} {signal:>8s} (>{threshold:.2f})")

        if abs(momentum) > threshold:
            signals_ready += 1

    print(f"\nSignals above threshold: {signals_ready}/{len(symbols)}")

    print("\n3. STRATEGY CONFIGURATION CHECK")
    print("-"*80)

    print(f"Momentum Threshold:  {strategy.momentum_threshold}")
    print(f"Volume Min:          {strategy.volume_min}")
    print(f"Expiry Hours:        {strategy.expiry_hours}")
    print(f"Variance Threshold:  {strategy.variance_threshold}")
    print(f"Base Position Size:  {strategy.base_position_size}")

    print("\n4. SIGNAL GENERATION TEST")
    print("-"*80)

    # Check if any signals would be generated with current data
    test_signals = []
    for symbol in symbols:
        factors = sentiment_factor.get_factors(symbol)
        momentum = factors.get('sentiment_momentum', 0)

        if abs(momentum) > strategy.momentum_threshold:
            direction = 'LONG' if momentum > 0 else 'SHORT'
            test_signals.append((symbol, direction, momentum))

    if test_signals:
        print("✅ Active signals detected:")
        for sym, direction, mom in test_signals:
            print(f"   {sym}: {direction} (momentum: {mom:+.3f})")
    else:
        print("⚠️  No signals above threshold")
        print("\nReasons (check each):")
        print("  1. Momentum too low (need >0.3 or <-0.3)")
        print("  2. Not enough historical data (need 6+ hours)")
        print("  3. Low variance in recent news (all neutral)")

        # Find closest to threshold
        closest = []
        for symbol in symbols:
            factors = sentiment_factor.get_factors(symbol)
            momentum = factors.get('sentiment_momentum', 0)
            closest.append((symbol, momentum, abs(momentum)))

        closest.sort(key=lambda x: x[2], reverse=True)
        print(f"\n  Closest to threshold:")
        for sym, mom, abs_mom in closest[:3]:
            gap = strategy.momentum_threshold - abs_mom
            print(f"    {sym}: {mom:+.3f} (need {gap:+.3f} more)")

    print("\n5. RECOMMENDATIONS")
    print("-"*80)

    if total_articles == 0:
        print("❌ No articles in database")
        print("   → Start news collector and wait 5 minutes")
    elif signals_ready == 0:
        print("⚠️  Sentiment data exists but no signals")
        if total_articles < 20:
            print("   → Wait for more articles (have {}, need 20+)".format(total_articles))
        print("   → Option 1: Wait 6+ hours for momentum to build")
        print("   → Option 2: Lower threshold temporarily (0.3 → 0.15)")
        print("   → Option 3: Check if news is too neutral (low variance)")
    else:
        print("✅ System ready for sentiment-driven trading!")
        print(f"   → {signals_ready} symbols have strong signals")
        print("   → Strategy should start trading immediately")

    print("\n" + "="*80)

    return signals_ready > 0


if __name__ == "__main__":
    try:
        ready = validate_sentiment()
        sys.exit(0 if ready else 1)
    except Exception as e:
        print(f"\n❌ Validation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
