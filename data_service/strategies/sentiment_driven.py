import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np

from data_service.strategies.strategy_base import StrategyBase, Signal, register_strategy
from data_service.factors.factor_calculator import FactorCalculator
from data_service.ai.sentiment_factor import SentimentFactor

logger = logging.getLogger(__name__)

@register_strategy('sentiment_driven')
class SentimentDriven(StrategyBase):
    """
    Enhanced Sentiment-Driven Strategy with News Momentum Trading
    
    DESIGN DECISIONS & RATIONALE:
    1. Sentiment Momentum: Trades the *rate of change* of sentiment, not absolute levels.
       Captures market reaction to breaking news, which is more reliable than 
       sentiment levels which can be persistently biased.
    2. Volume Confirmation: Validates sentiment with price action. High volume 
       indicates institutional participation. Confidence is scaled by volume strength.
    3. Variance-Based Risk: High disagreeement in news sources indicates uncertainty.
       Reduces confidence when sentiment variance is high to prevent trading on 
       conflicting narratives.
    4. Signal Decay: Sentiment moves are ephemeral. Full confidence in first 2 hours,
       gradual decay from 2-4 hours, and hard expiry at 4 hours.
    5. Momentum Persistence: Strong continued momentum refreshes signals, allowing 
       riding multi-day sentiment waves while preventing premature exits.
    6. Timestamp Robustness: Optimized for backtesting with reliable timestamp extraction.
    """

    def __init__(self):
        super().__init__('sentiment_driven')
        self.factor_calculator = FactorCalculator()
        self.sentiment_factor = SentimentFactor()
        
        # Strategy Parameters
        self.momentum_threshold = self.config.get('momentum_threshold', 0.3)
        self.volume_min = self.config.get('volume_min', 0.8)  # Minimum volume to trade
        self.volume_boost_threshold = self.config.get('volume_boost_threshold', 1.5)
        self.expiry_hours = self.config.get('expiry_hours', 4)
        self.decay_start_hours = self.config.get('decay_start_hours', 2)
        self.variance_threshold = self.config.get('variance_threshold', 0.2)
        self.base_position_size = self.config.get('base_position_size', 0.10)
        
        # State tracking
        self.signal_cache: Dict[str, Dict[str, Any]] = {}
        
        logger.info(f"Initialized {self.name} with momentum_threshold={self.momentum_threshold}, "
                   f"expiry={self.expiry_hours}h, decay_start={self.decay_start_hours}h")

    def _extract_timestamp(self, df: pd.DataFrame) -> datetime:
        """Robust timestamp extraction for backtesting compatibility."""
        if not df.empty and 'time' in df.columns:
            try:
                return pd.to_datetime(df['time'].iloc[-1], unit='ms')
            except:
                pass
        if not df.empty and isinstance(df.index, pd.DatetimeIndex):
            return df.index[-1]
        if not df.empty and 'timestamp' in df.columns:
            try:
                return pd.to_datetime(df['timestamp'].iloc[-1], unit='s')
            except:
                pass
        return datetime.now()

    def _calculate_time_decay(self, hours_elapsed: float) -> float:
        """
        Calculate confidence decay based on time elapsed.
        - 0-2 hours: Full confidence (1.0)
        - 2-4 hours: Linear decay to 0.5
        - 4+ hours: Signal expired
        """
        if hours_elapsed <= self.decay_start_hours:
            return 1.0
        elif hours_elapsed <= self.expiry_hours:
            # Linear decay from 1.0 to 0.5
            decay_range = self.expiry_hours - self.decay_start_hours
            decay_amount = (hours_elapsed - self.decay_start_hours) / decay_range
            return 1.0 - (0.5 * decay_amount)
        else:
            return 0.0  # Expired

    async def calculate_signals(self, market_data: Dict[str, pd.DataFrame], factors: Dict[str, Any]) -> Dict[str, Signal]:
        """
        Generate sentiment-driven trading signals with multi-factor confirmation.
        """
        signals = {}
        fetcher = factors.get('fetcher')

        for symbol, df in market_data.items():
            # === DATA VALIDATION ===
            if df.empty:
                signals[symbol] = Signal(symbol, 'flat', 0.0, "Empty dataframe")
                continue
            
            if len(df) < 20:  # Need some history for volume ratio
                signals[symbol] = Signal(symbol, 'flat', 0.0, "Insufficient data")
                continue

            now = self._extract_timestamp(df)

            # === CALCULATE FACTORS ===
            # Technical factors (volume)
            f_tech = await self.factor_calculator.calculate(df, symbol=symbol, fetcher=fetcher)
            vol_ratio = f_tech.get('volume_ratio_1h', 1.0)

            # Sentiment factors with robust error handling
            sentiment_available = True
            try:
                sf = self.sentiment_factor.get_factors(symbol)
                if sf is None:
                    sf = {}
                    sentiment_available = False
                momentum = sf.get('sentiment_momentum', 0.0)
                variance = sf.get('sentiment_variance', 0.0)
            except Exception as e:
                logger.warning(f"Sentiment factor unavailable for {symbol}: {e}")
                momentum = 0.0
                variance = 0.0
                sentiment_available = False

            # Handle NaN values - convert to safe defaults
            if pd.isna(momentum):
                momentum = 0.0
                sentiment_available = False
            if pd.isna(variance):
                variance = 0.0
            if pd.isna(vol_ratio):
                vol_ratio = 1.0

            # If no sentiment data, return flat signal with clear rationale
            if not sentiment_available:
                signals[symbol] = Signal(symbol, 'flat', 0.0, "Sentiment data unavailable - staying flat")
                continue

            # === SIGNAL GENERATION ===
            direction = 'flat'
            confidence = 0.0
            rationale_parts = []

            # Check if we have a cached signal
            cached = self.signal_cache.get(symbol, {})
            cached_direction = cached.get('direction', 'flat')
            cached_time = cached.get('timestamp', now)
            hours_elapsed = (now - cached_time).total_seconds() / 3600

            # 1. CHECK EXPIRY
            is_expired = hours_elapsed > self.expiry_hours
            time_decay = self._calculate_time_decay(hours_elapsed)

            # 2. DETERMINE DIRECTION
            # Strong momentum signal (fresh or continuation)
            if abs(momentum) > self.momentum_threshold:
                if momentum > self.momentum_threshold:
                    direction = 'long'
                    rationale_parts.append(f"Bullish momentum ({momentum:.2f})")
                else:
                    direction = 'short'
                    rationale_parts.append(f"Bearish momentum ({momentum:.2f})")
                
                # Fresh signal or continuation of same direction
                if direction == cached_direction and not is_expired:
                    rationale_parts.append("momentum sustained")
                else:
                    rationale_parts.append("new signal")
                    hours_elapsed = 0  # Reset for new signal
                    time_decay = 1.0
                
                # Base confidence: stronger momentum = higher confidence
                momentum_strength = min(abs(momentum) / 0.5, 1.0)  # Cap at 0.5 momentum
                confidence = 0.70 + (0.15 * momentum_strength)
                
            # Weak momentum: maintain cached signal if not expired
            elif not is_expired and cached_direction != 'flat':
                # Check if momentum is still in same direction (weaker threshold)
                weak_threshold = self.momentum_threshold * 0.3
                if (cached_direction == 'long' and momentum > weak_threshold) or \
                   (cached_direction == 'short' and momentum < -weak_threshold):
                    direction = cached_direction
                    confidence = 0.50  # Lower confidence for weak continuation
                    rationale_parts.append(f"weak momentum continuation ({momentum:.2f})")
                else:
                    # Momentum reversed or went flat - exit
                    direction = 'flat'
                    rationale_parts.append("momentum faded")
            else:
                rationale_parts.append("no momentum signal")

            # === APPLY FILTERS AND ADJUSTMENTS ===
            if direction != 'flat':
                
                # 3. VOLUME CONFIRMATION
                if vol_ratio < self.volume_min:
                    # Insufficient volume - reject signal
                    direction = 'flat'
                    rationale_parts.append(f"low volume ({vol_ratio:.2f}x)")
                else:
                    # Volume scaling
                    if vol_ratio > self.volume_boost_threshold:
                        vol_boost = min(0.15, (vol_ratio - 1.0) * 0.08)
                        confidence += vol_boost
                        rationale_parts.append(f"high vol ({vol_ratio:.2f}x)")
                    else:
                        rationale_parts.append(f"vol OK ({vol_ratio:.2f}x)")
                
                # 4. VARIANCE PENALTY (conflicting sentiment sources)
                if variance > self.variance_threshold:
                    variance_penalty = min(0.30, variance * 0.5)
                    confidence -= variance_penalty
                    rationale_parts.append(f"high variance ({variance:.2f})")
                
                # 5. TIME DECAY
                if time_decay < 1.0:
                    confidence *= time_decay
                    rationale_parts.append(f"decaying ({hours_elapsed:.1f}h old)")

            # === FINALIZE SIGNAL ===
            confidence = float(np.clip(confidence, 0.0, 1.0))
            
            # Minimum confidence threshold
            if direction != 'flat' and confidence < 0.35:
                direction = 'flat'
                rationale_parts.append("insufficient confidence")
            
            rationale = "; ".join(rationale_parts) if rationale_parts else "Neutral"
            
            logger.debug(f"{symbol}: {direction.upper()} @ {confidence:.2f} | {rationale}")
            
            # Update cache
            if direction != 'flat':
                self.signal_cache[symbol] = {
                    'direction': direction,
                    'timestamp': now if hours_elapsed == 0 else cached_time,
                    'initial_momentum': momentum
                }
            elif symbol in self.signal_cache and is_expired:
                # Clear expired signals
                del self.signal_cache[symbol]
            
            signals[symbol] = Signal(
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                rationale=rationale,
                generated_at=now
            )

        return signals

    def size_positions(self, signals: Dict[str, Signal], risk_params: Any) -> Dict[str, float]:
        """
        Position sizing based on confidence.
        """
        target_positions = {}
        total_exposure = 0.0
        
        for sym, sig in signals.items():
            if sig.direction == 'long':
                position_size = self.base_position_size * sig.confidence
                target_positions[sym] = position_size
                total_exposure += position_size
            elif sig.direction == 'short':
                position_size = self.base_position_size * sig.confidence
                target_positions[sym] = -position_size
                total_exposure += position_size
            else:
                target_positions[sym] = 0.0
        
        # Scale down if total exposure exceeds limit (default 40%)
        max_total_exposure = self.config.get('max_total_exposure', 0.40)
        if total_exposure > max_total_exposure:
            scale_factor = max_total_exposure / total_exposure
            logger.warning(f"Scaling positions by {scale_factor:.2f} to maintain {max_total_exposure:.0%} max exposure")
            target_positions = {k: v * scale_factor for k, v in target_positions.items()}
        
        return target_positions

    def generate_orders(self, positions: Dict[str, float], current_prices: Dict[str, float]) -> List[Any]:
        return []

    def reset_state(self):
        """Reset strategy state for backtesting."""
        self.signal_cache.clear()
        logger.info(f"{self.name} state reset")

    # HELPERS FOR VERIFICATION
    def calculate_signals_with_override(self, sentiment_momentum: float, volume_ratio: float) -> Signal:
        """Mock version for verification."""
        direction = 'flat'
        confidence = 0.0
        
        if abs(sentiment_momentum) > self.momentum_threshold and volume_ratio >= self.volume_min:
            direction = 'long' if sentiment_momentum > 0 else 'short'
            confidence = 0.8
        
        return Signal('XAU', direction, confidence, "Mock check")

    def inject_signal_for_test(self, symbol: str, direction: str, hours_ago: float):
        """Inject a signal for testing expiry/decay logic."""
        self.signal_cache[symbol] = {
            'direction': direction,
            'timestamp': datetime.now() - timedelta(hours=hours_ago),
            'initial_momentum': 0.5 if direction == 'long' else -0.5
        }

    def check_time_decay(self, hours_elapsed: float) -> float:
        """Test helper for time decay calculation."""
        return self._calculate_time_decay(hours_elapsed)
