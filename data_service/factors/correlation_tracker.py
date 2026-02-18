"""
Correlation Tracker

Tracks rolling correlations between traded symbols to:
1. Identify highly correlated positions (treat as combined exposure)
2. Calculate effective portfolio exposure (not just sum of positions)
3. Alert when portfolio diversification breaks down
4. Provide correlation-aware position limits

Key insight: If XAU and XAG have 0.85 correlation, holding both
at 20% each is NOT 40% diversified exposure - it's closer to 37%
effective exposure because they move together.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CorrelationPair:
    """Represents correlation between two symbols."""
    symbol_a: str
    symbol_b: str
    correlation: float
    is_high_correlation: bool  # > threshold
    lookback_days: int

    def __str__(self):
        return f"{self.symbol_a}/{self.symbol_b}: {self.correlation:.2f}"


@dataclass
class CorrelationState:
    """Current correlation state for the portfolio."""
    # Full correlation matrix
    correlation_matrix: pd.DataFrame

    # High correlation pairs (above threshold)
    high_correlation_pairs: List[CorrelationPair]

    # Average portfolio correlation (diversification measure)
    avg_portfolio_correlation: float

    # Effective exposure multipliers per symbol
    # If NVDA is correlated with AMD, holding both increases effective exposure
    effective_exposure_multipliers: Dict[str, float]

    # Portfolio diversification score (0-1, higher = more diversified)
    diversification_score: float

    # Timestamp of calculation
    calculated_at: datetime

    # Symbols included
    symbols: List[str]

    def get_correlation(self, symbol_a: str, symbol_b: str) -> float:
        """Get correlation between two symbols."""
        if symbol_a not in self.correlation_matrix.index:
            return 0.0
        if symbol_b not in self.correlation_matrix.columns:
            return 0.0
        return float(self.correlation_matrix.loc[symbol_a, symbol_b])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "high_correlation_pairs": [str(p) for p in self.high_correlation_pairs],
            "avg_portfolio_correlation": self.avg_portfolio_correlation,
            "diversification_score": self.diversification_score,
            "effective_exposure_multipliers": self.effective_exposure_multipliers,
            "symbols": self.symbols,
            "calculated_at": self.calculated_at.isoformat(),
        }


class CorrelationTracker:
    """
    Tracks and analyzes correlations between traded symbols.

    Uses rolling return correlations to identify:
    - Highly correlated pairs that should be treated as combined exposure
    - Portfolio-level diversification
    - Effective exposure considering correlations
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}

        # Correlation thresholds
        self.high_correlation_threshold = config.get("high_correlation_threshold", 0.70)
        self.warning_correlation_threshold = config.get("warning_correlation_threshold", 0.85)

        # Calculation parameters
        self.lookback_days = config.get("lookback_days", 30)
        self.min_data_points = config.get("min_data_points", 20)

        # Cache for stability
        self._cache: Optional[CorrelationState] = None
        self._cache_time: Optional[datetime] = None
        self.cache_duration_minutes = config.get("cache_duration_minutes", 60)

        # Known correlation groups (for faster risk assessment)
        self.correlation_groups = {
            "metals": ["XAU", "XAG", "HG"],
            "tech_mega": ["AAPL", "GOOGL", "MSFT", "AMZN", "META"],
            "tech_growth": ["NVDA", "AMD", "TSLA"],
            "crypto": ["BTC", "ETH", "SOL", "COIN"],
        }

        logger.info(f"CorrelationTracker initialized: threshold={self.high_correlation_threshold}, "
                   f"lookback={self.lookback_days}d")

    def calculate(self, market_data: Dict[str, pd.DataFrame]) -> CorrelationState:
        """
        Calculate correlation state from market data.

        Args:
            market_data: Dict mapping symbol -> OHLCV DataFrame

        Returns:
            CorrelationState with matrix and analysis
        """
        # Check cache
        now = datetime.now()
        if self._cache and self._cache_time:
            cache_age = (now - self._cache_time).total_seconds() / 60
            if cache_age < self.cache_duration_minutes:
                return self._cache

        symbols = list(market_data.keys())

        if len(symbols) < 2:
            return self._empty_state(symbols)

        try:
            # Build returns DataFrame
            returns_data = {}
            for symbol, df in market_data.items():
                if len(df) < self.min_data_points:
                    continue

                # Calculate daily returns (use close prices)
                close = df['close'].iloc[-self.lookback_days * 24:] if len(df) > self.lookback_days * 24 else df['close']

                # Resample to daily if hourly data
                if len(close) > self.lookback_days:
                    # Check if index is DatetimeIndex
                    if isinstance(close.index, pd.DatetimeIndex):
                        close_daily = close.resample('D').last().dropna()
                    elif 'time' in df.columns:
                        # Use time column to create proper index
                        close_copy = close.copy()
                        time_slice = df['time'].iloc[-len(close):]
                        close_copy.index = pd.to_datetime(time_slice.values, unit='ms')
                        close_daily = close_copy.resample('D').last().dropna()
                    else:
                        # Fallback: take every 24th point as "daily"
                        close_daily = close.iloc[::24]
                else:
                    close_daily = close

                returns = close_daily.pct_change().dropna()

                if len(returns) >= self.min_data_points:
                    returns_data[symbol] = returns

            if len(returns_data) < 2:
                return self._empty_state(symbols)

            # Align all returns to common index
            returns_df = pd.DataFrame(returns_data)
            returns_df = returns_df.dropna()

            if len(returns_df) < self.min_data_points:
                return self._empty_state(symbols)

            # Calculate correlation matrix
            corr_matrix = returns_df.corr()

            # Find high correlation pairs
            high_corr_pairs = self._find_high_correlation_pairs(corr_matrix)

            # Calculate portfolio-level metrics
            avg_corr = self._calculate_avg_correlation(corr_matrix)
            diversification = self._calculate_diversification_score(corr_matrix)

            # Calculate effective exposure multipliers
            exposure_multipliers = self._calculate_exposure_multipliers(
                corr_matrix, high_corr_pairs
            )

            state = CorrelationState(
                correlation_matrix=corr_matrix,
                high_correlation_pairs=high_corr_pairs,
                avg_portfolio_correlation=avg_corr,
                effective_exposure_multipliers=exposure_multipliers,
                diversification_score=diversification,
                calculated_at=now,
                symbols=list(corr_matrix.columns),
            )

            # Update cache
            self._cache = state
            self._cache_time = now

            # Log warnings for very high correlations
            for pair in high_corr_pairs:
                if pair.correlation > self.warning_correlation_threshold:
                    logger.warning(f"High correlation detected: {pair.symbol_a}/{pair.symbol_b} = {pair.correlation:.2f}")

            return state

        except Exception as e:
            logger.error(f"Correlation calculation error: {e}")
            return self._empty_state(symbols)

    def _find_high_correlation_pairs(self, corr_matrix: pd.DataFrame) -> List[CorrelationPair]:
        """Find all pairs with correlation above threshold."""
        pairs = []
        symbols = list(corr_matrix.columns)

        for i, sym_a in enumerate(symbols):
            for sym_b in symbols[i+1:]:
                corr = corr_matrix.loc[sym_a, sym_b]

                if not np.isnan(corr) and abs(corr) > self.high_correlation_threshold:
                    pairs.append(CorrelationPair(
                        symbol_a=sym_a,
                        symbol_b=sym_b,
                        correlation=float(corr),
                        is_high_correlation=True,
                        lookback_days=self.lookback_days,
                    ))

        # Sort by correlation (highest first)
        pairs.sort(key=lambda x: abs(x.correlation), reverse=True)
        return pairs

    def _calculate_avg_correlation(self, corr_matrix: pd.DataFrame) -> float:
        """Calculate average off-diagonal correlation."""
        n = len(corr_matrix)
        if n < 2:
            return 0.0

        # Get upper triangle (excluding diagonal)
        mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)
        upper_triangle = corr_matrix.values[mask]

        # Remove NaN values
        valid_corrs = upper_triangle[~np.isnan(upper_triangle)]

        if len(valid_corrs) == 0:
            return 0.0

        return float(np.mean(np.abs(valid_corrs)))

    def _calculate_diversification_score(self, corr_matrix: pd.DataFrame) -> float:
        """
        Calculate portfolio diversification score.

        Score = 1 - avg_correlation
        Higher = more diversified (less correlated)

        Returns value between 0 (perfectly correlated) and 1 (uncorrelated)
        """
        avg_corr = self._calculate_avg_correlation(corr_matrix)
        return float(1.0 - avg_corr)

    def _calculate_exposure_multipliers(
        self,
        corr_matrix: pd.DataFrame,
        high_corr_pairs: List[CorrelationPair]
    ) -> Dict[str, float]:
        """
        Calculate effective exposure multiplier for each symbol.

        If a symbol is highly correlated with others in the portfolio,
        its effective exposure is higher than its nominal exposure.

        Example: If you hold NVDA and AMD at 20% each with 0.8 correlation,
        effective exposure for each is ~1.4x (they move together).
        """
        multipliers = {}
        symbols = list(corr_matrix.columns)

        for symbol in symbols:
            # Base multiplier is 1.0
            multiplier = 1.0

            # Add contribution from correlated assets
            for pair in high_corr_pairs:
                if pair.symbol_a == symbol or pair.symbol_b == symbol:
                    # Higher correlation = higher effective exposure
                    # Scale: 0.7 corr -> 1.1x, 0.85 corr -> 1.25x, 1.0 corr -> 1.5x
                    corr_contribution = (pair.correlation - self.high_correlation_threshold) / (1.0 - self.high_correlation_threshold)
                    multiplier += 0.5 * corr_contribution

            multipliers[symbol] = min(multiplier, 2.0)  # Cap at 2x

        return multipliers

    def get_effective_exposure(
        self,
        positions: Dict[str, float],
        correlation_state: Optional[CorrelationState] = None
    ) -> Dict[str, Any]:
        """
        Calculate effective portfolio exposure considering correlations.

        Args:
            positions: Dict mapping symbol -> position size (as % of equity)
            correlation_state: Pre-calculated state (optional)

        Returns:
            Dict with nominal exposure, effective exposure, and breakdown
        """
        if correlation_state is None:
            correlation_state = self._cache

        if correlation_state is None:
            # No correlation data, return nominal
            total_nominal = sum(abs(p) for p in positions.values())
            return {
                "nominal_exposure": total_nominal,
                "effective_exposure": total_nominal,
                "exposure_ratio": 1.0,
                "by_symbol": {s: abs(p) for s, p in positions.items()},
            }

        # Calculate nominal exposure
        total_nominal = sum(abs(p) for p in positions.values())

        # Calculate effective exposure using multipliers
        effective_by_symbol = {}
        total_effective = 0.0

        for symbol, position in positions.items():
            multiplier = correlation_state.effective_exposure_multipliers.get(symbol, 1.0)
            effective = abs(position) * multiplier
            effective_by_symbol[symbol] = effective
            total_effective += effective

        # Avoid double-counting: cap at reasonable level
        # If everything is perfectly correlated, effective = nominal
        # If uncorrelated, effective can be less than sum
        total_effective = min(total_effective, total_nominal * 1.5)

        return {
            "nominal_exposure": total_nominal,
            "effective_exposure": total_effective,
            "exposure_ratio": total_effective / total_nominal if total_nominal > 0 else 1.0,
            "by_symbol": effective_by_symbol,
            "diversification_score": correlation_state.diversification_score,
        }

    def should_reduce_position(
        self,
        symbol: str,
        existing_positions: Dict[str, float],
        proposed_size: float,
        max_effective_exposure: float = 1.5,
        correlation_state: Optional[CorrelationState] = None
    ) -> Tuple[bool, str, float]:
        """
        Check if adding a position would create excessive correlated exposure.

        Args:
            symbol: Symbol to add
            existing_positions: Current positions
            proposed_size: Proposed position size
            max_effective_exposure: Maximum allowed effective exposure
            correlation_state: Pre-calculated state

        Returns:
            (should_reduce, reason, suggested_size)
        """
        if correlation_state is None:
            correlation_state = self._cache

        if correlation_state is None:
            return False, "No correlation data", proposed_size

        # Calculate exposure with proposed position
        test_positions = existing_positions.copy()
        test_positions[symbol] = test_positions.get(symbol, 0) + proposed_size

        exposure = self.get_effective_exposure(test_positions, correlation_state)

        if exposure["effective_exposure"] <= max_effective_exposure:
            return False, "OK", proposed_size

        # Find correlated symbols
        correlated_symbols = []
        for pair in correlation_state.high_correlation_pairs:
            if pair.symbol_a == symbol:
                correlated_symbols.append((pair.symbol_b, pair.correlation))
            elif pair.symbol_b == symbol:
                correlated_symbols.append((pair.symbol_a, pair.correlation))

        if correlated_symbols:
            corr_list = ", ".join([f"{s}({c:.2f})" for s, c in correlated_symbols])
            reason = f"Correlated with existing: {corr_list}"
        else:
            reason = "Would exceed max effective exposure"

        # Suggest reduced size
        current_effective = self.get_effective_exposure(existing_positions, correlation_state)
        room = max_effective_exposure - current_effective["effective_exposure"]
        multiplier = correlation_state.effective_exposure_multipliers.get(symbol, 1.0)
        suggested = max(0, room / multiplier)

        return True, reason, suggested

    def get_correlation_groups(self, symbols: List[str]) -> Dict[str, List[str]]:
        """
        Group symbols by their correlations.

        Returns dict mapping group_name -> list of symbols in that group.
        Useful for understanding portfolio concentration.
        """
        result = {}

        for group_name, group_symbols in self.correlation_groups.items():
            matching = [s for s in symbols if s in group_symbols]
            if matching:
                result[group_name] = matching

        # Add "other" group for unmatched symbols
        all_grouped = set()
        for syms in result.values():
            all_grouped.update(syms)

        other = [s for s in symbols if s not in all_grouped]
        if other:
            result["other"] = other

        return result

    def _empty_state(self, symbols: List[str]) -> CorrelationState:
        """Return empty correlation state when calculation fails."""
        return CorrelationState(
            correlation_matrix=pd.DataFrame(),
            high_correlation_pairs=[],
            avg_portfolio_correlation=0.0,
            effective_exposure_multipliers={s: 1.0 for s in symbols},
            diversification_score=1.0,
            calculated_at=datetime.now(),
            symbols=symbols,
        )

    def log_correlation_summary(self, state: Optional[CorrelationState] = None):
        """Log a summary of current correlations."""
        if state is None:
            state = self._cache

        if state is None or state.correlation_matrix.empty:
            logger.info("No correlation data available")
            return

        logger.info("=" * 50)
        logger.info("CORRELATION SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Symbols: {', '.join(state.symbols)}")
        logger.info(f"Avg Portfolio Correlation: {state.avg_portfolio_correlation:.2f}")
        logger.info(f"Diversification Score: {state.diversification_score:.2f}")

        if state.high_correlation_pairs:
            logger.info(f"\nHigh Correlation Pairs (>{self.high_correlation_threshold}):")
            for pair in state.high_correlation_pairs[:10]:  # Top 10
                logger.info(f"  {pair.symbol_a}/{pair.symbol_b}: {pair.correlation:.2f}")
        else:
            logger.info("\nNo high correlation pairs detected")

        logger.info("=" * 50)
