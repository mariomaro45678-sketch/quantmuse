"""
Dynamic Position Sizer (Phase 3.2)

Regime-aware and correlation-aware position sizing that adjusts based on:
1. Market regime (trending/ranging/high-vol)
2. Strategy type (momentum/mean-reversion/sentiment)
3. Correlation exposure (reduces size when correlated positions exist)
4. Equity constraints (min order size, max exposure)

Integrates with:
- RegimeDetector: For market condition awareness
- CorrelationTracker: For effective exposure calculation
- RiskManager: For position limits
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

from data_service.factors.regime_detector import RegimeState, MarketRegime
from data_service.factors.correlation_tracker import CorrelationState, CorrelationTracker
from data_service.risk.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class StrategyType(Enum):
    """Strategy classification for sizing adjustments."""
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    SENTIMENT = "sentiment"
    OTHER = "other"


@dataclass
class SizingResult:
    """Result of dynamic position sizing."""
    raw_size: float           # Initial size from strategy
    adjusted_size: float      # After all adjustments
    regime_multiplier: float  # From regime detection
    correlation_multiplier: float  # From correlation exposure
    final_multiplier: float   # Combined multiplier
    min_size_applied: bool    # Whether min order size was applied
    capped: bool              # Whether any cap was hit
    rationale: str            # Explanation of adjustments


class DynamicSizer:
    """
    Dynamic position sizing with regime and correlation awareness.

    Combines multiple factors to determine optimal position size:
    - Regime detection adjusts based on market conditions
    - Correlation tracker reduces exposure when positions are correlated
    - Equity constraints ensure min/max limits are respected
    """

    # Default multipliers by regime (from roadmap spec)
    REGIME_MULTIPLIERS = {
        # (momentum_mult, mean_rev_mult, sentiment_mult, overall_mult)
        MarketRegime.TRENDING_UP: (1.2, 0.6, 1.1, 1.0),
        MarketRegime.TRENDING_DOWN: (1.2, 0.6, 0.9, 1.0),
        MarketRegime.RANGING: (0.7, 1.3, 0.8, 1.0),
        MarketRegime.HIGH_VOL: (0.7, 0.7, 0.6, 0.7),  # Reduce all
        MarketRegime.LOW_VOL: (0.9, 1.1, 1.0, 1.0),
        MarketRegime.UNKNOWN: (1.0, 1.0, 1.0, 1.0),
    }

    # Correlation exposure limits
    CORRELATION_THRESHOLDS = {
        "high": 0.7,    # Correlation above this triggers adjustment
        "moderate": 0.5,
    }

    # Position limits
    MIN_ORDER_NOTIONAL = 10.0  # HIP-3 minimum
    MAX_SINGLE_POSITION_PCT = 0.25  # 25% of equity max per position
    MAX_CORRELATED_EXPOSURE_PCT = 0.40  # 40% effective exposure for correlated group

    def __init__(
        self,
        risk_manager: Optional[RiskManager] = None,
        correlation_tracker: Optional[CorrelationTracker] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        self.risk_mgr = risk_manager
        self.correlation_tracker = correlation_tracker
        self.config = config or {}

        # Override defaults from config
        self.min_order_notional = self.config.get("min_order_notional", self.MIN_ORDER_NOTIONAL)
        self.max_single_position_pct = self.config.get("max_single_position_pct", self.MAX_SINGLE_POSITION_PCT)
        self.max_correlated_exposure_pct = self.config.get("max_correlated_exposure_pct", self.MAX_CORRELATED_EXPOSURE_PCT)

        # Track current positions for correlation calculations
        self._current_positions: Dict[str, float] = {}  # symbol -> size_pct

        logger.info(f"DynamicSizer initialized: min_notional=${self.min_order_notional}, "
                   f"max_single={self.max_single_position_pct:.0%}, "
                   f"max_corr_exposure={self.max_correlated_exposure_pct:.0%}")

    def classify_strategy(self, strategy_name: str) -> StrategyType:
        """Classify strategy for appropriate multiplier selection."""
        name_lower = strategy_name.lower()
        if "momentum" in name_lower:
            return StrategyType.MOMENTUM
        elif "mean_reversion" in name_lower or "reversion" in name_lower:
            return StrategyType.MEAN_REVERSION
        elif "sentiment" in name_lower:
            return StrategyType.SENTIMENT
        else:
            return StrategyType.OTHER

    def get_regime_multiplier(
        self,
        regime_state: Optional[RegimeState],
        strategy_type: StrategyType
    ) -> Tuple[float, str]:
        """
        Get the regime-based multiplier for a strategy type.

        Returns (multiplier, reason)
        """
        if regime_state is None:
            return 1.0, "no regime data"

        regime = regime_state.regime
        mults = self.REGIME_MULTIPLIERS.get(regime, (1.0, 1.0, 1.0, 1.0))

        # Select appropriate multiplier based on strategy type
        if strategy_type == StrategyType.MOMENTUM:
            base_mult = mults[0]
        elif strategy_type == StrategyType.MEAN_REVERSION:
            base_mult = mults[1]
        elif strategy_type == StrategyType.SENTIMENT:
            base_mult = mults[2]
        else:
            base_mult = 1.0

        # Apply overall regime multiplier
        overall_mult = mults[3]
        final_mult = base_mult * overall_mult

        # Scale by confidence
        final_mult = 1.0 + (final_mult - 1.0) * regime_state.confidence

        reason = f"{regime.value} ({regime_state.confidence:.0%} conf)"
        return final_mult, reason

    def get_correlation_multiplier(
        self,
        symbol: str,
        target_size_pct: float,
        correlation_state: Optional[CorrelationState] = None
    ) -> Tuple[float, str]:
        """
        Get correlation-based multiplier to prevent overexposure to correlated assets.

        If the symbol is highly correlated with existing positions, reduce size.

        Returns (multiplier, reason)
        """
        if not correlation_state or not self._current_positions:
            return 1.0, "no correlation data"

        # Find correlated pairs for this symbol using correlation_matrix
        correlated_symbols = []
        for other in self._current_positions:
            if other == symbol:
                continue
            try:
                corr = abs(correlation_state.get_correlation(symbol, other))
                if corr > self.CORRELATION_THRESHOLDS["moderate"]:
                    correlated_symbols.append((other, corr))
            except Exception:
                continue

        if not correlated_symbols:
            return 1.0, "no correlated positions"

        # Calculate effective exposure of correlated positions
        effective_exposure = 0.0
        for other_sym, corr in correlated_symbols:
            other_size = abs(self._current_positions.get(other_sym, 0))
            # Effective exposure = size * correlation
            effective_exposure += other_size * corr

        # If adding this position would exceed max correlated exposure, reduce size
        proposed_total = effective_exposure + abs(target_size_pct)
        if proposed_total > self.max_correlated_exposure_pct:
            # Calculate multiplier to bring within limits
            allowed_addition = max(0, self.max_correlated_exposure_pct - effective_exposure)
            if abs(target_size_pct) > 0:
                multiplier = allowed_addition / abs(target_size_pct)
                multiplier = max(0.3, min(1.0, multiplier))  # Floor at 30%
                pairs_str = ", ".join([f"{s}({c:.0%})" for s, c in correlated_symbols[:2]])
                return multiplier, f"correlated with {pairs_str}"

        return 1.0, "correlation OK"

    def calculate_size(
        self,
        symbol: str,
        raw_size_pct: float,
        current_price: float,
        equity: float,
        strategy_name: str,
        regime_state: Optional[RegimeState] = None,
        correlation_state: Optional[CorrelationState] = None,
        signal_confidence: float = 1.0
    ) -> SizingResult:
        """
        Calculate adjusted position size with all factors applied.

        Args:
            symbol: Trading symbol
            raw_size_pct: Raw position size as % of equity (from strategy)
            current_price: Current market price
            equity: Current equity
            strategy_name: Name of the strategy
            regime_state: Current market regime
            correlation_state: Current correlation data
            signal_confidence: Signal confidence (0-1)

        Returns:
            SizingResult with adjusted size and explanation
        """
        strategy_type = self.classify_strategy(strategy_name)
        rationale_parts = []
        capped = False
        min_applied = False

        # 1. Get regime multiplier
        regime_mult, regime_reason = self.get_regime_multiplier(regime_state, strategy_type)
        if regime_mult != 1.0:
            rationale_parts.append(f"regime:{regime_reason}={regime_mult:.2f}x")

        # 2. Get correlation multiplier
        corr_mult, corr_reason = self.get_correlation_multiplier(
            symbol, raw_size_pct, correlation_state
        )
        if corr_mult != 1.0:
            rationale_parts.append(f"corr:{corr_reason}={corr_mult:.2f}x")

        # 3. Apply signal confidence scaling (gentle: 0.7-1.0 range)
        # Previous 0.5-1.0 range was too aggressive - cut sizes in half at moderate confidence
        confidence_mult = 0.7 + (signal_confidence * 0.3)  # Range: 0.7-1.0

        # 4. Calculate final multiplier
        final_mult = regime_mult * corr_mult * confidence_mult

        # 5. Apply to raw size
        adjusted_size_pct = raw_size_pct * final_mult

        # 6. Apply max single position cap
        if abs(adjusted_size_pct) > self.max_single_position_pct:
            adjusted_size_pct = self.max_single_position_pct * (1 if adjusted_size_pct > 0 else -1)
            rationale_parts.append(f"capped at {self.max_single_position_pct:.0%}")
            capped = True

        # 7. Convert to notional and check minimum
        notional = abs(adjusted_size_pct) * equity
        if notional > 0 and notional < self.min_order_notional:
            # Check if we can meet minimum with reasonable leverage
            # For small accounts, we might need to size up to minimum
            min_size_pct = self.min_order_notional / equity
            if min_size_pct <= self.max_single_position_pct:
                adjusted_size_pct = min_size_pct * (1 if adjusted_size_pct > 0 else -1)
                rationale_parts.append(f"min notional ${self.min_order_notional}")
                min_applied = True
            else:
                # Can't meet minimum without exceeding max position size
                adjusted_size_pct = 0.0
                rationale_parts.append("below min, skipped")

        rationale = "; ".join(rationale_parts) if rationale_parts else "no adjustments"

        return SizingResult(
            raw_size=raw_size_pct,
            adjusted_size=adjusted_size_pct,
            regime_multiplier=regime_mult,
            correlation_multiplier=corr_mult,
            final_multiplier=final_mult,
            min_size_applied=min_applied,
            capped=capped,
            rationale=rationale
        )

    def update_positions(self, positions: Dict[str, float]):
        """
        Update tracked positions for correlation calculations.

        Args:
            positions: Dict of symbol -> position size as % of equity
        """
        self._current_positions = positions.copy()

    def size_portfolio(
        self,
        target_positions: Dict[str, float],
        current_prices: Dict[str, float],
        equity: float,
        strategy_name: str,
        regime_state: Optional[RegimeState] = None,
        correlation_state: Optional[CorrelationState] = None,
        signal_confidences: Optional[Dict[str, float]] = None
    ) -> Dict[str, SizingResult]:
        """
        Size a complete portfolio of target positions.

        Args:
            target_positions: Dict of symbol -> target position % of equity
            current_prices: Dict of symbol -> current price
            equity: Current equity
            strategy_name: Strategy name
            regime_state: Current regime
            correlation_state: Current correlations
            signal_confidences: Optional dict of symbol -> confidence

        Returns:
            Dict of symbol -> SizingResult
        """
        results = {}
        confidences = signal_confidences or {}

        for symbol, target_pct in target_positions.items():
            price = current_prices.get(symbol, 0)
            if price <= 0:
                continue

            confidence = confidences.get(symbol, 1.0)

            result = self.calculate_size(
                symbol=symbol,
                raw_size_pct=target_pct,
                current_price=price,
                equity=equity,
                strategy_name=strategy_name,
                regime_state=regime_state,
                correlation_state=correlation_state,
                signal_confidence=confidence
            )

            results[symbol] = result

        return results

    def get_effective_exposure(
        self,
        positions: Dict[str, float],
        correlation_state: Optional[CorrelationState] = None
    ) -> Dict[str, Any]:
        """
        Calculate effective portfolio exposure accounting for correlations.

        High correlations mean positions add more to effective risk.

        Returns dict with:
        - gross_exposure: Sum of absolute positions
        - effective_exposure: Correlation-adjusted exposure
        - diversification_benefit: How much correlation reduces risk
        """
        if not positions:
            return {
                "gross_exposure": 0.0,
                "effective_exposure": 0.0,
                "diversification_benefit": 0.0
            }

        gross = sum(abs(p) for p in positions.values())

        if not correlation_state or len(positions) < 2:
            return {
                "gross_exposure": gross,
                "effective_exposure": gross,
                "diversification_benefit": 0.0
            }

        # Use correlation tracker's effective exposure calculation if available
        if self.correlation_tracker:
            result = self.correlation_tracker.get_effective_exposure(
                positions, correlation_state
            )
            return result

        # Simple fallback: assume average correlation of 0.5
        avg_corr = 0.5
        effective = gross * (1 - (1 - avg_corr) * 0.3)  # Partial diversification

        return {
            "gross_exposure": gross,
            "effective_exposure": effective,
            "diversification_benefit": (gross - effective) / gross if gross > 0 else 0
        }

    def get_sizing_summary(self, results: Dict[str, SizingResult]) -> str:
        """Generate a summary of sizing decisions."""
        if not results:
            return "No positions sized"

        lines = ["Position Sizing Summary:"]
        for symbol, result in results.items():
            if result.adjusted_size != 0:
                lines.append(
                    f"  {symbol}: {result.raw_size:.1%} -> {result.adjusted_size:.1%} "
                    f"({result.final_multiplier:.2f}x) [{result.rationale}]"
                )
        return "\n".join(lines)
