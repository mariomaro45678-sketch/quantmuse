"""
Strategy Ensemble Coordinator

Coordinates signals across multiple strategies to:
1. Boost confidence when strategies agree on direction for the same symbol
2. Handle conflicts intelligently (higher confidence wins, or go flat if uncertain)
3. Track portfolio-wide sentiment for context

Agreement Boosting:
- 2 strategies agree on same symbol: confidence *= 1.15
- 3 strategies agree: confidence *= 1.30

Conflict Resolution:
- Take higher confidence signal
- If both > 0.6 confidence and conflict: go flat (uncertainty)

Portfolio Sentiment:
- Track % of signals long vs short across all strategies
- Provides context for risk management
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class StrategySignal:
    """Signal from a single strategy."""
    strategy_name: str
    symbol: str
    direction: str  # 'long', 'short', 'flat'
    confidence: float
    generated_at: datetime = field(default_factory=datetime.now)


@dataclass
class EnsembledSignal:
    """Final signal after ensemble processing."""
    symbol: str
    direction: str
    confidence: float
    contributing_strategies: List[str]
    agreement_count: int
    conflict_resolved: bool
    adjustment_reason: str
    original_confidence: float


@dataclass
class PortfolioSentiment:
    """Overall portfolio sentiment across all strategies."""
    long_count: int
    short_count: int
    flat_count: int
    bullish_pct: float  # % of non-flat signals that are long
    bearish_pct: float  # % of non-flat signals that are short
    sentiment: str  # 'bullish', 'bearish', 'mixed', 'neutral'
    avg_confidence: float


class EnsembleCoordinator:
    """
    Coordinates signals across multiple strategies for enhanced decision-making.

    When multiple strategies signal the same symbol:
    - Agreement boosts confidence
    - Conflicts are resolved by confidence level or go flat

    Also tracks overall portfolio sentiment to inform risk decisions.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}

        # Agreement multipliers
        self.two_agree_multiplier = config.get("two_agree_multiplier", 1.15)
        self.three_agree_multiplier = config.get("three_agree_multiplier", 1.30)

        # Conflict resolution
        self.conflict_flat_threshold = config.get("conflict_flat_threshold", 0.6)

        # Portfolio sentiment thresholds
        self.bullish_threshold = config.get("bullish_threshold", 0.65)
        self.bearish_threshold = config.get("bearish_threshold", 0.35)

        # Current signals by strategy
        self._signals_by_strategy: Dict[str, Dict[str, StrategySignal]] = {}
        self._last_update: Dict[str, datetime] = {}

        # Signal staleness (ignore signals older than this)
        self.signal_ttl_seconds = config.get("signal_ttl_seconds", 300)

        logger.info(f"EnsembleCoordinator initialized: 2-agree={self.two_agree_multiplier}x, "
                   f"3-agree={self.three_agree_multiplier}x")

    def update_signals(self, strategy_name: str, signals: Dict[str, Any]):
        """
        Update signals from a strategy.

        Args:
            strategy_name: Name of the strategy
            signals: Dict mapping symbol -> Signal object (with direction, confidence)
        """
        now = datetime.now()
        self._signals_by_strategy[strategy_name] = {}

        for symbol, sig in signals.items():
            # Handle both Signal objects and dicts
            if hasattr(sig, 'direction'):
                direction = sig.direction
                confidence = sig.confidence
                generated_at = getattr(sig, 'generated_at', now)
            else:
                direction = sig.get('direction', 'flat')
                confidence = sig.get('confidence', 0.0)
                generated_at = sig.get('generated_at', now)

            self._signals_by_strategy[strategy_name][symbol] = StrategySignal(
                strategy_name=strategy_name,
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                generated_at=generated_at,
            )

        self._last_update[strategy_name] = now
        logger.debug(f"Updated signals from {strategy_name}: {len(signals)} symbols")

    def get_ensembled_signals(self) -> Dict[str, EnsembledSignal]:
        """
        Get ensembled signals across all strategies.

        Returns dict mapping symbol -> EnsembledSignal with adjusted confidence.
        """
        now = datetime.now()

        # Group signals by symbol
        signals_by_symbol: Dict[str, List[StrategySignal]] = defaultdict(list)

        for strategy_name, signals in self._signals_by_strategy.items():
            # Check if signals are fresh
            last_update = self._last_update.get(strategy_name)
            if last_update:
                age = (now - last_update).total_seconds()
                if age > self.signal_ttl_seconds:
                    logger.debug(f"Ignoring stale signals from {strategy_name} (age={age:.0f}s)")
                    continue

            for symbol, sig in signals.items():
                signals_by_symbol[symbol].append(sig)

        # Process each symbol
        result = {}
        for symbol, symbol_signals in signals_by_symbol.items():
            ensembled = self._ensemble_symbol(symbol, symbol_signals)
            result[symbol] = ensembled

        return result

    def _ensemble_symbol(self, symbol: str, signals: List[StrategySignal]) -> EnsembledSignal:
        """
        Ensemble multiple signals for a single symbol.
        """
        if len(signals) == 0:
            return EnsembledSignal(
                symbol=symbol,
                direction='flat',
                confidence=0.0,
                contributing_strategies=[],
                agreement_count=0,
                conflict_resolved=False,
                adjustment_reason="no signals",
                original_confidence=0.0,
            )

        if len(signals) == 1:
            sig = signals[0]
            return EnsembledSignal(
                symbol=symbol,
                direction=sig.direction,
                confidence=sig.confidence,
                contributing_strategies=[sig.strategy_name],
                agreement_count=1,
                conflict_resolved=False,
                adjustment_reason="single strategy",
                original_confidence=sig.confidence,
            )

        # Multiple signals - check for agreement/conflict
        non_flat = [s for s in signals if s.direction != 'flat']

        if len(non_flat) == 0:
            # All flat
            return EnsembledSignal(
                symbol=symbol,
                direction='flat',
                confidence=0.0,
                contributing_strategies=[s.strategy_name for s in signals],
                agreement_count=len(signals),
                conflict_resolved=False,
                adjustment_reason="all strategies flat",
                original_confidence=0.0,
            )

        # Count directions
        longs = [s for s in non_flat if s.direction == 'long']
        shorts = [s for s in non_flat if s.direction == 'short']

        if len(longs) > 0 and len(shorts) > 0:
            # Conflict
            return self._resolve_conflict(symbol, longs, shorts, signals)
        else:
            # Agreement
            agreeing = longs if len(longs) > 0 else shorts
            return self._boost_agreement(symbol, agreeing, signals)

    def _boost_agreement(
        self,
        symbol: str,
        agreeing_signals: List[StrategySignal],
        all_signals: List[StrategySignal]
    ) -> EnsembledSignal:
        """
        Boost confidence when strategies agree.
        """
        # Use highest confidence as base
        best = max(agreeing_signals, key=lambda s: s.confidence)
        original_conf = best.confidence

        # Apply agreement multiplier
        agreement_count = len(agreeing_signals)
        if agreement_count >= 3:
            multiplier = self.three_agree_multiplier
            reason = f"3+ strategies agree ({agreement_count}x boost)"
        elif agreement_count == 2:
            multiplier = self.two_agree_multiplier
            reason = f"2 strategies agree ({multiplier}x boost)"
        else:
            multiplier = 1.0
            reason = "single strategy"

        new_conf = min(1.0, original_conf * multiplier)

        return EnsembledSignal(
            symbol=symbol,
            direction=best.direction,
            confidence=new_conf,
            contributing_strategies=[s.strategy_name for s in agreeing_signals],
            agreement_count=agreement_count,
            conflict_resolved=False,
            adjustment_reason=reason,
            original_confidence=original_conf,
        )

    def _resolve_conflict(
        self,
        symbol: str,
        longs: List[StrategySignal],
        shorts: List[StrategySignal],
        all_signals: List[StrategySignal]
    ) -> EnsembledSignal:
        """
        Resolve conflicting signals.
        """
        best_long = max(longs, key=lambda s: s.confidence)
        best_short = max(shorts, key=lambda s: s.confidence)

        # If both are high confidence, go flat (uncertainty)
        if (best_long.confidence > self.conflict_flat_threshold and
            best_short.confidence > self.conflict_flat_threshold):
            return EnsembledSignal(
                symbol=symbol,
                direction='flat',
                confidence=0.0,
                contributing_strategies=[s.strategy_name for s in all_signals],
                agreement_count=0,
                conflict_resolved=True,
                adjustment_reason=f"conflict: both >60% conf, going flat",
                original_confidence=max(best_long.confidence, best_short.confidence),
            )

        # Take higher confidence signal
        if best_long.confidence > best_short.confidence:
            winner = best_long
            loser_conf = best_short.confidence
        else:
            winner = best_short
            loser_conf = best_long.confidence

        # Reduce confidence by the loser's confidence (penalty for conflict)
        penalty = loser_conf * 0.5
        new_conf = max(0.0, winner.confidence - penalty)

        return EnsembledSignal(
            symbol=symbol,
            direction=winner.direction,
            confidence=new_conf,
            contributing_strategies=[winner.strategy_name],
            agreement_count=1,
            conflict_resolved=True,
            adjustment_reason=f"conflict resolved: {winner.strategy_name} wins (-{penalty:.2f} penalty)",
            original_confidence=winner.confidence,
        )

    def get_portfolio_sentiment(self) -> PortfolioSentiment:
        """
        Calculate overall portfolio sentiment across all strategies.
        """
        now = datetime.now()
        long_count = 0
        short_count = 0
        flat_count = 0
        total_confidence = 0.0
        total_signals = 0

        for strategy_name, signals in self._signals_by_strategy.items():
            # Check freshness
            last_update = self._last_update.get(strategy_name)
            if last_update:
                age = (now - last_update).total_seconds()
                if age > self.signal_ttl_seconds:
                    continue

            for symbol, sig in signals.items():
                if sig.direction == 'long':
                    long_count += 1
                    total_confidence += sig.confidence
                elif sig.direction == 'short':
                    short_count += 1
                    total_confidence += sig.confidence
                else:
                    flat_count += 1

                total_signals += 1

        non_flat = long_count + short_count
        if non_flat == 0:
            bullish_pct = 0.5
            bearish_pct = 0.5
            sentiment = 'neutral'
        else:
            bullish_pct = long_count / non_flat
            bearish_pct = short_count / non_flat

            if bullish_pct >= self.bullish_threshold:
                sentiment = 'bullish'
            elif bearish_pct >= self.bullish_threshold:
                sentiment = 'bearish'
            else:
                sentiment = 'mixed'

        avg_conf = total_confidence / non_flat if non_flat > 0 else 0.0

        return PortfolioSentiment(
            long_count=long_count,
            short_count=short_count,
            flat_count=flat_count,
            bullish_pct=bullish_pct,
            bearish_pct=bearish_pct,
            sentiment=sentiment,
            avg_confidence=avg_conf,
        )

    def should_reduce_exposure(self, sentiment: PortfolioSentiment) -> Tuple[bool, str]:
        """
        Check if portfolio sentiment suggests reducing exposure.

        Returns (should_reduce, reason)
        """
        # High flat count = uncertainty
        total = sentiment.long_count + sentiment.short_count + sentiment.flat_count
        if total > 0:
            flat_pct = sentiment.flat_count / total
            if flat_pct > 0.7:
                return True, f"high uncertainty ({flat_pct:.0%} flat signals)"

        # Mixed sentiment with low confidence
        if sentiment.sentiment == 'mixed' and sentiment.avg_confidence < 0.5:
            return True, f"mixed sentiment with low confidence ({sentiment.avg_confidence:.2f})"

        return False, ""

    def get_strategy_correlation(self) -> Dict[str, Dict[str, float]]:
        """
        Calculate correlation of signals between strategies.

        Returns matrix of strategy name -> strategy name -> correlation
        """
        strategies = list(self._signals_by_strategy.keys())
        if len(strategies) < 2:
            return {}

        correlations = {}
        for i, strat_a in enumerate(strategies):
            correlations[strat_a] = {}
            signals_a = self._signals_by_strategy.get(strat_a, {})

            for strat_b in strategies:
                if strat_a == strat_b:
                    correlations[strat_a][strat_b] = 1.0
                    continue

                signals_b = self._signals_by_strategy.get(strat_b, {})

                # Find common symbols
                common = set(signals_a.keys()) & set(signals_b.keys())
                if not common:
                    correlations[strat_a][strat_b] = 0.0
                    continue

                # Calculate agreement rate
                agreements = 0
                for sym in common:
                    dir_a = signals_a[sym].direction
                    dir_b = signals_b[sym].direction
                    if dir_a == dir_b:
                        agreements += 1
                    elif dir_a == 'flat' or dir_b == 'flat':
                        agreements += 0.5  # Partial agreement

                correlations[strat_a][strat_b] = agreements / len(common)

        return correlations

    def log_ensemble_summary(self, ensembled: Dict[str, EnsembledSignal]):
        """Log a summary of ensemble decisions."""
        if not ensembled:
            logger.info("No ensembled signals")
            return

        logger.info("=" * 50)
        logger.info("ENSEMBLE SIGNAL SUMMARY")
        logger.info("=" * 50)

        boosted = 0
        conflicts = 0

        for symbol, sig in ensembled.items():
            if sig.agreement_count >= 2:
                boosted += 1
            if sig.conflict_resolved:
                conflicts += 1

            if sig.direction != 'flat':
                delta = sig.confidence - sig.original_confidence
                delta_str = f" ({delta:+.2f})" if delta != 0 else ""
                logger.info(f"  {symbol}: {sig.direction.upper()} @ {sig.confidence:.2f}{delta_str} "
                           f"| {sig.adjustment_reason}")

        logger.info(f"\n  Boosted: {boosted} | Conflicts resolved: {conflicts}")

        # Portfolio sentiment
        sentiment = self.get_portfolio_sentiment()
        logger.info(f"  Portfolio: {sentiment.sentiment.upper()} "
                   f"(L:{sentiment.long_count} S:{sentiment.short_count} F:{sentiment.flat_count})")
        logger.info("=" * 50)

    def clear(self):
        """Clear all stored signals."""
        self._signals_by_strategy.clear()
        self._last_update.clear()
