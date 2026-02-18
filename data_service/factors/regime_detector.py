"""
Market Regime Detector

Classifies current market conditions into regimes that inform strategy behavior:
- TRENDING_UP: Strong upward trend (momentum strategies excel)
- TRENDING_DOWN: Strong downward trend (momentum strategies excel)
- RANGING: Sideways/choppy market (mean reversion strategies excel)
- HIGH_VOL: Elevated volatility (reduce position sizes)
- LOW_VOL: Compressed volatility (potential breakout setup)

Uses three primary indicators:
1. ADX (Average Directional Index) - Trend strength
2. ATR Percentile - Volatility relative to recent history
3. Hurst Exponent - Trending vs mean-reverting behavior
"""

import logging
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOL = "high_vol"
    LOW_VOL = "low_vol"
    UNKNOWN = "unknown"


@dataclass
class RegimeState:
    """Current regime classification with supporting metrics."""
    regime: MarketRegime
    confidence: float  # 0-1, how confident we are in this classification
    adx: float
    adx_direction: str  # "up" or "down" based on +DI vs -DI
    atr_percentile: float  # 0-1, current ATR vs 100-period history
    hurst: float  # <0.5 = mean-reverting, >0.5 = trending
    volatility_state: str  # "high", "normal", "low"

    # Strategy multipliers (how to adjust strategy weights)
    momentum_multiplier: float  # Scale momentum strategy sizing
    mean_reversion_multiplier: float  # Scale mean reversion strategy sizing
    position_size_multiplier: float  # Overall position size adjustment

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime.value,
            "confidence": self.confidence,
            "adx": self.adx,
            "adx_direction": self.adx_direction,
            "atr_percentile": self.atr_percentile,
            "hurst": self.hurst,
            "volatility_state": self.volatility_state,
            "momentum_multiplier": self.momentum_multiplier,
            "mean_reversion_multiplier": self.mean_reversion_multiplier,
            "position_size_multiplier": self.position_size_multiplier,
        }


class RegimeDetector:
    """
    Detects market regime from OHLCV data.

    Thresholds are configurable but defaults are based on empirical research:
    - ADX > 25: Trending market
    - ADX > 40: Strong trend
    - ADX < 20: Weak/no trend (ranging)
    - Hurst > 0.55: Trending behavior
    - Hurst < 0.45: Mean-reverting behavior
    - ATR percentile > 75%: High volatility
    - ATR percentile < 25%: Low volatility
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}

        # ADX thresholds
        self.adx_trending_threshold = config.get("adx_trending_threshold", 25)
        self.adx_strong_trend_threshold = config.get("adx_strong_trend_threshold", 40)
        self.adx_ranging_threshold = config.get("adx_ranging_threshold", 20)

        # Hurst thresholds
        self.hurst_trending_threshold = config.get("hurst_trending_threshold", 0.55)
        self.hurst_mean_revert_threshold = config.get("hurst_mean_revert_threshold", 0.45)

        # Volatility thresholds (percentiles)
        self.vol_high_percentile = config.get("vol_high_percentile", 75)
        self.vol_low_percentile = config.get("vol_low_percentile", 25)

        # Lookback periods
        self.atr_period = config.get("atr_period", 14)
        self.atr_history_period = config.get("atr_history_period", 100)
        self.hurst_period = config.get("hurst_period", 100)
        self.adx_period = config.get("adx_period", 14)

        # Cache for regime stability (avoid flipping on noise)
        self._last_regime: Dict[str, MarketRegime] = {}
        self._regime_count: Dict[str, int] = {}
        self.regime_stability_threshold = config.get("regime_stability_threshold", 3)

        logger.info(f"RegimeDetector initialized: ADX trending>{self.adx_trending_threshold}, "
                   f"Hurst trending>{self.hurst_trending_threshold}")

    def detect(self, df: pd.DataFrame, symbol: str = "DEFAULT") -> RegimeState:
        """
        Detect market regime from OHLCV DataFrame.

        Args:
            df: DataFrame with columns ['open', 'high', 'low', 'close', 'volume']
            symbol: Symbol identifier for regime caching

        Returns:
            RegimeState with classification and metrics
        """
        if df is None or len(df) < self.hurst_period:
            return self._unknown_regime()

        try:
            # Calculate indicators
            adx, plus_di, minus_di = self._calculate_adx(df)
            atr_percentile = self._calculate_atr_percentile(df)
            hurst = self._calculate_hurst(df)

            # Determine direction from DI lines
            adx_direction = "up" if plus_di > minus_di else "down"

            # Determine volatility state
            if atr_percentile > self.vol_high_percentile / 100:
                volatility_state = "high"
            elif atr_percentile < self.vol_low_percentile / 100:
                volatility_state = "low"
            else:
                volatility_state = "normal"

            # Classify regime
            regime, confidence = self._classify_regime(
                adx, adx_direction, hurst, atr_percentile
            )

            # Apply stability filter (don't flip regime on single bar)
            regime = self._apply_stability_filter(symbol, regime)

            # Calculate strategy multipliers
            momentum_mult, mean_rev_mult, pos_size_mult = self._calculate_multipliers(
                regime, confidence, volatility_state
            )

            state = RegimeState(
                regime=regime,
                confidence=confidence,
                adx=adx,
                adx_direction=adx_direction,
                atr_percentile=atr_percentile,
                hurst=hurst,
                volatility_state=volatility_state,
                momentum_multiplier=momentum_mult,
                mean_reversion_multiplier=mean_rev_mult,
                position_size_multiplier=pos_size_mult,
            )

            logger.debug(f"[{symbol}] Regime: {regime.value} (conf={confidence:.2f}) | "
                        f"ADX={adx:.1f} ({adx_direction}) | Hurst={hurst:.2f} | "
                        f"ATR%={atr_percentile:.0%} | Vol={volatility_state}")

            return state

        except Exception as e:
            logger.warning(f"Regime detection error for {symbol}: {e}")
            return self._unknown_regime()

    def _calculate_adx(self, df: pd.DataFrame) -> tuple[float, float, float]:
        """Calculate ADX and directional indicators."""
        high = df['high']
        low = df['low']
        close = df['close']
        period = self.adx_period

        # True Range
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)

        # Directional Movement
        up_move = high.diff()
        down_move = -low.diff()

        plus_dm = pd.Series(
            np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
            index=df.index
        )
        minus_dm = pd.Series(
            np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
            index=df.index
        )

        # Smoothed averages (Wilder's smoothing approximated with EMA)
        tr_smooth = tr.ewm(span=period, adjust=False).mean()
        plus_dm_smooth = plus_dm.ewm(span=period, adjust=False).mean()
        minus_dm_smooth = minus_dm.ewm(span=period, adjust=False).mean()

        # Directional Indicators
        plus_di = 100 * plus_dm_smooth / tr_smooth.replace(0, np.nan)
        minus_di = 100 * minus_dm_smooth / tr_smooth.replace(0, np.nan)

        # ADX
        di_sum = plus_di + minus_di
        dx = 100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)
        adx = dx.ewm(span=period, adjust=False).mean()

        # Return latest values
        adx_val = float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 25.0
        plus_di_val = float(plus_di.iloc[-1]) if not np.isnan(plus_di.iloc[-1]) else 25.0
        minus_di_val = float(minus_di.iloc[-1]) if not np.isnan(minus_di.iloc[-1]) else 25.0

        return adx_val, plus_di_val, minus_di_val

    def _calculate_atr_percentile(self, df: pd.DataFrame) -> float:
        """Calculate current ATR as percentile of historical ATR."""
        high = df['high']
        low = df['low']
        close = df['close']

        # True Range
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)

        # Current ATR
        current_atr = tr.rolling(window=self.atr_period).mean().iloc[-1]

        # Historical ATR values
        historical_atr = tr.rolling(window=self.atr_period).mean().iloc[-self.atr_history_period:]

        # Percentile rank
        if len(historical_atr) < 20:
            return 0.5  # Not enough data, assume normal

        percentile = (historical_atr < current_atr).sum() / len(historical_atr)
        return float(percentile)

    def _calculate_hurst(self, df: pd.DataFrame) -> float:
        """
        Calculate Hurst exponent using R/S (Rescaled Range) method.

        H > 0.5: Trending/persistent (momentum works)
        H = 0.5: Random walk
        H < 0.5: Mean-reverting/anti-persistent (mean reversion works)
        """
        prices = df['close'].iloc[-self.hurst_period:].values

        if len(prices) < 20:
            return 0.5  # Not enough data

        # Calculate returns
        returns = np.diff(np.log(prices))

        # Use multiple sub-periods for R/S calculation
        max_k = min(len(returns) // 4, 50)
        if max_k < 4:
            return 0.5

        rs_values = []
        n_values = []

        for n in range(10, max_k + 1):
            # Number of sub-periods
            num_periods = len(returns) // n
            if num_periods < 1:
                continue

            rs_list = []
            for i in range(num_periods):
                subset = returns[i * n:(i + 1) * n]

                # Mean-adjusted cumulative sum
                mean_adj = subset - np.mean(subset)
                cumsum = np.cumsum(mean_adj)

                # Range
                R = np.max(cumsum) - np.min(cumsum)

                # Standard deviation
                S = np.std(subset, ddof=1)

                if S > 0:
                    rs_list.append(R / S)

            if rs_list:
                rs_values.append(np.mean(rs_list))
                n_values.append(n)

        if len(rs_values) < 3:
            return 0.5

        # Log-log regression to find Hurst exponent
        log_n = np.log(n_values)
        log_rs = np.log(rs_values)

        # Simple linear regression
        slope, _ = np.polyfit(log_n, log_rs, 1)

        # Clamp to reasonable range
        hurst = float(np.clip(slope, 0.0, 1.0))

        return hurst

    def _classify_regime(
        self,
        adx: float,
        adx_direction: str,
        hurst: float,
        atr_percentile: float
    ) -> tuple[MarketRegime, float]:
        """
        Classify regime based on indicators.

        Returns (regime, confidence)
        """
        confidence = 0.5  # Base confidence

        # Primary classification based on ADX + Hurst agreement
        is_trending_adx = adx > self.adx_trending_threshold
        is_strong_trend = adx > self.adx_strong_trend_threshold
        is_ranging_adx = adx < self.adx_ranging_threshold

        is_trending_hurst = hurst > self.hurst_trending_threshold
        is_mean_reverting_hurst = hurst < self.hurst_mean_revert_threshold

        # High/Low volatility override
        if atr_percentile > 0.85:  # Very high vol
            regime = MarketRegime.HIGH_VOL
            confidence = 0.7 + (atr_percentile - 0.85) * 2  # 0.7-1.0
        elif atr_percentile < 0.15:  # Very low vol
            regime = MarketRegime.LOW_VOL
            confidence = 0.7 + (0.15 - atr_percentile) * 2

        # Trending classification
        elif is_trending_adx and is_trending_hurst:
            # Strong agreement on trending
            if adx_direction == "up":
                regime = MarketRegime.TRENDING_UP
            else:
                regime = MarketRegime.TRENDING_DOWN

            confidence = 0.6
            if is_strong_trend:
                confidence += 0.2
            if hurst > 0.6:
                confidence += 0.1

        elif is_trending_adx:
            # ADX says trending, Hurst ambiguous
            if adx_direction == "up":
                regime = MarketRegime.TRENDING_UP
            else:
                regime = MarketRegime.TRENDING_DOWN
            confidence = 0.5

        # Ranging classification
        elif is_ranging_adx and is_mean_reverting_hurst:
            # Strong agreement on ranging
            regime = MarketRegime.RANGING
            confidence = 0.7
            if hurst < 0.4:
                confidence += 0.1

        elif is_ranging_adx or is_mean_reverting_hurst:
            # One indicator says ranging
            regime = MarketRegime.RANGING
            confidence = 0.5

        else:
            # Ambiguous - default based on recent price action
            if adx_direction == "up":
                regime = MarketRegime.TRENDING_UP
            else:
                regime = MarketRegime.TRENDING_DOWN
            confidence = 0.4

        return regime, min(confidence, 1.0)

    def _apply_stability_filter(self, symbol: str, regime: MarketRegime) -> MarketRegime:
        """
        Prevent rapid regime flipping by requiring consecutive signals.
        """
        last = self._last_regime.get(symbol)

        if regime == last:
            # Same regime, increment counter
            self._regime_count[symbol] = self._regime_count.get(symbol, 0) + 1
        else:
            # Different regime, check if we should flip
            count = self._regime_count.get(symbol, 0)
            if count >= self.regime_stability_threshold:
                # Old regime was stable, start counting new regime
                self._regime_count[symbol] = 1
                self._last_regime[symbol] = regime
            else:
                # Old regime not stable yet, keep old one
                if last is not None:
                    regime = last
                else:
                    self._last_regime[symbol] = regime
                    self._regime_count[symbol] = 1

        return regime

    def _calculate_multipliers(
        self,
        regime: MarketRegime,
        confidence: float,
        volatility_state: str
    ) -> tuple[float, float, float]:
        """
        Calculate strategy weight multipliers based on regime.

        Returns (momentum_mult, mean_reversion_mult, position_size_mult)
        """
        # Base multipliers by regime
        multipliers = {
            MarketRegime.TRENDING_UP: (1.2, 0.6, 1.0),
            MarketRegime.TRENDING_DOWN: (1.2, 0.6, 1.0),
            MarketRegime.RANGING: (0.7, 1.3, 1.0),
            MarketRegime.HIGH_VOL: (0.8, 0.8, 0.7),  # Reduce both + size
            MarketRegime.LOW_VOL: (0.9, 1.1, 1.0),  # Slight mean-rev bias
            MarketRegime.UNKNOWN: (1.0, 1.0, 1.0),
        }

        mom_mult, mr_mult, pos_mult = multipliers.get(regime, (1.0, 1.0, 1.0))

        # Scale by confidence (less confident = closer to 1.0)
        mom_mult = 1.0 + (mom_mult - 1.0) * confidence
        mr_mult = 1.0 + (mr_mult - 1.0) * confidence
        pos_mult = 1.0 + (pos_mult - 1.0) * confidence

        # Additional volatility adjustment
        if volatility_state == "high":
            pos_mult *= 0.85

        return mom_mult, mr_mult, pos_mult

    def _unknown_regime(self) -> RegimeState:
        """Return default unknown regime state."""
        return RegimeState(
            regime=MarketRegime.UNKNOWN,
            confidence=0.0,
            adx=25.0,
            adx_direction="up",
            atr_percentile=0.5,
            hurst=0.5,
            volatility_state="normal",
            momentum_multiplier=1.0,
            mean_reversion_multiplier=1.0,
            position_size_multiplier=1.0,
        )

    def get_regime_summary(self, market_data: Dict[str, pd.DataFrame]) -> Dict[str, RegimeState]:
        """
        Get regime for all symbols in market data.

        Args:
            market_data: Dict mapping symbol -> OHLCV DataFrame

        Returns:
            Dict mapping symbol -> RegimeState
        """
        results = {}
        for symbol, df in market_data.items():
            results[symbol] = self.detect(df, symbol)
        return results

    def get_portfolio_regime(self, market_data: Dict[str, pd.DataFrame]) -> RegimeState:
        """
        Calculate aggregate portfolio-level regime.

        Uses majority voting weighted by confidence.
        """
        regimes = self.get_regime_summary(market_data)

        if not regimes:
            return self._unknown_regime()

        # Weighted voting
        regime_scores: Dict[MarketRegime, float] = {}
        total_adx = 0.0
        total_hurst = 0.0
        total_atr_pct = 0.0
        total_confidence = 0.0

        for symbol, state in regimes.items():
            weight = state.confidence
            regime_scores[state.regime] = regime_scores.get(state.regime, 0) + weight
            total_adx += state.adx * weight
            total_hurst += state.hurst * weight
            total_atr_pct += state.atr_percentile * weight
            total_confidence += weight

        if total_confidence == 0:
            return self._unknown_regime()

        # Winning regime
        winning_regime = max(regime_scores, key=regime_scores.get)
        winning_confidence = regime_scores[winning_regime] / total_confidence

        # Weighted averages
        avg_adx = total_adx / total_confidence
        avg_hurst = total_hurst / total_confidence
        avg_atr_pct = total_atr_pct / total_confidence

        # Determine volatility state
        if avg_atr_pct > 0.75:
            vol_state = "high"
        elif avg_atr_pct < 0.25:
            vol_state = "low"
        else:
            vol_state = "normal"

        # Calculate multipliers
        mom_mult, mr_mult, pos_mult = self._calculate_multipliers(
            winning_regime, winning_confidence, vol_state
        )

        return RegimeState(
            regime=winning_regime,
            confidence=winning_confidence,
            adx=avg_adx,
            adx_direction="mixed",
            atr_percentile=avg_atr_pct,
            hurst=avg_hurst,
            volatility_state=vol_state,
            momentum_multiplier=mom_mult,
            mean_reversion_multiplier=mr_mult,
            position_size_multiplier=pos_mult,
        )
