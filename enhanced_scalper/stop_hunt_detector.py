"""
Stop Hunt Detection System
===========================
Detects stop hunts, liquidity sweeps, and false breakouts.

Features:
- Liquidity level identification
- Sweep detection above/below key levels
- False breakout confirmation
- Stop cluster estimation
- Fade opportunity identification
"""

import logging
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
from enum import Enum

logger = logging.getLogger(__name__)


class HuntType(Enum):
    NONE = "none"
    STOP_HUNT_LONG = "stop_hunt_long"  # Swept above highs
    STOP_HUNT_SHORT = "stop_hunt_short"  # Swept below lows
    LIQUIDITY_SWEEP = "liquidity_sweep"
    FALSE_BREAKOUT = "false_breakout"


@dataclass
class LiquidityLevel:
    """Liquidity level data."""
    price: float
    level_type: str  # 'high', 'low', 'support', 'resistance', 'round_number'
    strength: float  # 0-1 based on recency and number of touches
    last_touched: datetime
    touch_count: int = 0


@dataclass
class HuntSignal:
    """Detected hunt signal."""
    hunt_type: HuntType
    symbol: str
    timestamp: datetime
    trigger_price: float
    target_level: float
    
    # Analysis
    sweep_distance: float = 0.0  # How far price swept
    rejection_strength: float = 0.0  # How strong the rejection
    volume_confirmation: bool = False
    
    # Fade opportunity
    fade_direction: str = ""  # 'long' or 'short'
    entry_price: float = 0.0
    stop_loss: float = 0.0
    confidence: float = 0.0
    
    def is_valid_fade(self) -> bool:
        """Check if this is a valid fade opportunity."""
        return (
            self.hunt_type in [HuntType.STOP_HUNT_LONG, HuntType.STOP_HUNT_SHORT,
                             HuntType.LIQUIDITY_SWEEP, HuntType.FALSE_BREAKOUT] and
            self.confidence >= 0.6 and
            self.rejection_strength >= 0.5
        )


class StopHuntDetector:
    """
    Professional stop hunt detection system.
    
    Identifies when market makers trigger stop-loss clusters
    to generate liquidity for their own positions.
    """
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        
        # Detection thresholds
        self.sweep_threshold_pct = self.config.get('sweep_threshold_pct', 0.001)  # 0.1%
        self.rejection_threshold_pct = self.config.get('rejection_threshold_pct', 0.0005)  # 0.05%
        self.min_level_age_seconds = self.config.get('min_level_age_seconds', 300)  # 5 min
        
        # State
        self.liquidity_levels: Dict[str, List[LiquidityLevel]] = {}
        self.price_history: Dict[str, deque] = {}
        self.volume_history: Dict[str, deque] = {}
        self.recent_hunts: deque = deque(maxlen=20)
        
    def update_liquidity_levels(self, symbol: str, high: float, low: float,
                                volume: float, timestamp: datetime):
        """
        Update known liquidity levels from recent price action.
        
        Args:
            symbol: Trading symbol
            high: Recent high
            low: Recent low
            volume: Recent volume
            timestamp: Timestamp
        """
        if symbol not in self.liquidity_levels:
            self.liquidity_levels[symbol] = []
            self.price_history[symbol] = deque(maxlen=100)
            self.volume_history[symbol] = deque(maxlen=100)
        
        # Add to history
        self.price_history[symbol].append({'high': high, 'low': low, 'timestamp': timestamp})
        self.volume_history[symbol].append(volume)
        
        # Update or add levels
        levels = self.liquidity_levels[symbol]
        
        # Check if high already exists
        high_exists = False
        for level in levels:
            if abs(level.price - high) / high < 0.001:  # Within 0.1%
                level.touch_count += 1
                level.last_touched = timestamp
                level.strength = min(1.0, level.strength + 0.1)
                high_exists = True
                break
        
        if not high_exists:
            levels.append(LiquidityLevel(
                price=high,
                level_type='high',
                strength=0.3,
                last_touched=timestamp,
                touch_count=1
            ))
        
        # Check if low already exists
        low_exists = False
        for level in levels:
            if abs(level.price - low) / low < 0.001:
                level.touch_count += 1
                level.last_touched = timestamp
                level.strength = min(1.0, level.strength + 0.1)
                low_exists = True
                break
        
        if not low_exists:
            levels.append(LiquidityLevel(
                price=low,
                level_type='low',
                strength=0.3,
                last_touched=timestamp,
                touch_count=1
            ))
        
        # Clean old levels
        cutoff = timestamp - timedelta(seconds=3600)  # 1 hour
        self.liquidity_levels[symbol] = [
            l for l in levels 
            if l.last_touched > cutoff and l.strength > 0.1
        ]
    
    def detect_stop_hunt(self, symbol: str, current_price: float, 
                        timestamp: datetime, volume: float = 0) -> Optional[HuntSignal]:
        """
        Detect stop hunt patterns.
        
        Args:
            symbol: Trading symbol
            current_price: Current market price
            timestamp: Current timestamp
            volume: Recent volume
        
        Returns:
            HuntSignal if hunt detected, None otherwise
        """
        if symbol not in self.liquidity_levels:
            return None
        
        levels = self.liquidity_levels[symbol]
        
        # Check for sweep above highs (stop hunt longs)
        for level in levels:
            if level.level_type != 'high':
                continue
            
            # Check if price swept above this level
            sweep_distance = (current_price - level.price) / level.price
            
            if sweep_distance > self.sweep_threshold_pct:
                # Potential stop hunt - check for recent rejection
                rejection = self._check_rejection(symbol, current_price, level.price)
                
                if rejection['is_rejection']:
                    return self._create_hunt_signal(
                        HuntType.STOP_HUNT_LONG,
                        symbol,
                        timestamp,
                        current_price,
                        level.price,
                        sweep_distance,
                        rejection['strength'],
                        volume
                    )
        
        # Check for sweep below lows (stop hunt shorts)
        for level in levels:
            if level.level_type != 'low':
                continue
            
            sweep_distance = (level.price - current_price) / level.price
            
            if sweep_distance > self.sweep_threshold_pct:
                rejection = self._check_rejection(symbol, current_price, level.price)
                
                if rejection['is_rejection']:
                    return self._create_hunt_signal(
                        HuntType.STOP_HUNT_SHORT,
                        symbol,
                        timestamp,
                        current_price,
                        level.price,
                        sweep_distance,
                        rejection['strength'],
                        volume
                    )
        
        return None
    
    def _check_rejection(self, symbol: str, current_price: float,
                        level_price: float) -> Dict:
        """
        Check if price is showing rejection after sweep.
        
        Returns:
            Dict with 'is_rejection' and 'strength'
        """
        try:
            if symbol not in self.price_history:
                return {'is_rejection': False, 'strength': 0.0}
            
            history = list(self.price_history[symbol])
            if len(history) < 3:
                return {'is_rejection': False, 'strength': 0.0}
            
            # Get recent candles
            recent = history[-3:]
            
            # Check for wick rejection pattern
            # Price went beyond level but came back
            
            if current_price > level_price:
                # Swept above - check if coming back down
                max_breach = max(c.get('high', 0) for c in recent)
                current_vs_level = (current_price - level_price) / level_price
                max_vs_level = (max_breach - level_price) / level_price
                
                if max_vs_level > current_vs_level:
                    # Price came back from max breach
                    rejection_pct = (max_breach - current_price) / max_breach
                    
                    return {
                        'is_rejection': rejection_pct > self.rejection_threshold_pct,
                        'strength': min(1.0, rejection_pct * 10)
                    }
            
            else:
                # Swept below - check if coming back up
                min_breach = min(c.get('low', float('inf')) for c in recent)
                current_vs_level = (level_price - current_price) / level_price
                min_vs_level = (level_price - min_breach) / level_price
                
                if min_vs_level > current_vs_level:
                    rejection_pct = (current_price - min_breach) / min_breach
                    
                    return {
                        'is_rejection': rejection_pct > self.rejection_threshold_pct,
                        'strength': min(1.0, rejection_pct * 10)
                    }
            
            return {'is_rejection': False, 'strength': 0.0}
        
        except Exception as e:
            logger.warning(f"Rejection check error: {e}")
            return {'is_rejection': False, 'strength': 0.0}
    
    def _create_hunt_signal(self, hunt_type: HuntType, symbol: str,
                           timestamp: datetime, trigger_price: float,
                           target_level: float, sweep_distance: float,
                           rejection_strength: float, volume: float) -> HuntSignal:
        """Create hunt signal with fade opportunity."""
        signal = HuntSignal(
            hunt_type=hunt_type,
            symbol=symbol,
            timestamp=timestamp,
            trigger_price=trigger_price,
            target_level=target_level,
            sweep_distance=sweep_distance,
            rejection_strength=rejection_strength
        )
        
        # Determine fade direction
        if hunt_type == HuntType.STOP_HUNT_LONG:
            signal.fade_direction = 'short'
            signal.entry_price = target_level
            signal.stop_loss = trigger_price * 1.002  # 0.2% above sweep high
        else:
            signal.fade_direction = 'long'
            signal.entry_price = target_level
            signal.stop_loss = trigger_price * 0.998  # 0.2% below sweep low
        
        # Calculate confidence
        confidence_factors = [
            min(1.0, sweep_distance * 1000),  # Sweep distance
            rejection_strength,  # Rejection strength
            0.7 if volume > 0 else 0.5  # Volume confirmation
        ]
        
        signal.confidence = np.mean(confidence_factors)
        signal.volume_confirmation = volume > 0
        
        # Store hunt
        self.recent_hunts.append(signal)
        
        logger.info(f"Stop hunt detected: {hunt_type.value} on {symbol} "
                   f"at ${trigger_price:.2f} (conf={signal.confidence:.2f})")
        
        return signal
    
    def detect_false_breakout(self, symbol: str, support: float, resistance: float,
                             current_price: float, timestamp: datetime,
                             volume: float = 0) -> Optional[HuntSignal]:
        """
        Detect false breakout patterns.
        
        Args:
            symbol: Trading symbol
            support: Support level
            resistance: Resistance level
            current_price: Current price
            timestamp: Current timestamp
            volume: Recent volume
        
        Returns:
            HuntSignal if false breakout detected
        """
        # Check for false breakout above resistance
        if current_price > resistance:
            breach_pct = (current_price - resistance) / resistance
            
            if 0 < breach_pct < self.sweep_threshold_pct * 2:
                # Small breach - check for quick rejection
                rejection = self._check_rejection(symbol, current_price, resistance)
                
                if rejection['is_rejection'] and rejection['strength'] > 0.6:
                    signal = self._create_hunt_signal(
                        HuntType.FALSE_BREAKOUT,
                        symbol,
                        timestamp,
                        current_price,
                        resistance,
                        breach_pct,
                        rejection['strength'],
                        volume
                    )
                    signal.fade_direction = 'short'
                    return signal
        
        # Check for false breakdown below support
        if current_price < support:
            breach_pct = (support - current_price) / support
            
            if 0 < breach_pct < self.sweep_threshold_pct * 2:
                rejection = self._check_rejection(symbol, current_price, support)
                
                if rejection['is_rejection'] and rejection['strength'] > 0.6:
                    signal = self._create_hunt_signal(
                        HuntType.FALSE_BREAKOUT,
                        symbol,
                        timestamp,
                        current_price,
                        support,
                        breach_pct,
                        rejection['strength'],
                        volume
                    )
                    signal.fade_direction = 'long'
                    return signal
        
        return None
    
    def get_recent_hunts(self, symbol: str = None, 
                        lookback_seconds: int = 300) -> List[HuntSignal]:
        """
        Get recent hunt signals.
        
        Args:
            symbol: Filter by symbol (optional)
            lookback_seconds: How far back to look
        
        Returns:
            List of HuntSignal objects
        """
        cutoff = datetime.now() - timedelta(seconds=lookback_seconds)
        
        hunts = [h for h in self.recent_hunts if h.timestamp > cutoff]
        
        if symbol:
            hunts = [h for h in hunts if h.symbol == symbol]
        
        return hunts
    
    def get_liquidity_zones(self, symbol: str) -> Dict:
        """
        Get current liquidity zones for a symbol.
        
        Returns:
            Dict with liquidity analysis
        """
        if symbol not in self.liquidity_levels:
            return {
                'highs': [],
                'lows': [],
                'clusters_above': [],
                'clusters_below': []
            }
        
        levels = self.liquidity_levels[symbol]
        
        highs = [l for l in levels if l.level_type == 'high']
        lows = [l for l in levels if l.level_type == 'low']
        
        # Sort by strength
        highs.sort(key=lambda x: x.strength, reverse=True)
        lows.sort(key=lambda x: x.strength, reverse=True)
        
        return {
            'highs': [{'price': l.price, 'strength': l.strength, 
                      'touches': l.touch_count} for l in highs[:5]],
            'lows': [{'price': l.price, 'strength': l.strength,
                     'touches': l.touch_count} for l in lows[:5]]
        }
    
    def is_hunt_in_progress(self, symbol: str, lookback_seconds: int = 60) -> bool:
        """Check if a hunt was recently detected for symbol."""
        recent = self.get_recent_hunts(symbol, lookback_seconds)
        return len(recent) > 0


if __name__ == "__main__":
    # Test the detector
    detector = StopHuntDetector()
    
    from datetime import timedelta
    
    # Simulate price action
    base_price = 50000
    now = datetime.now()
    
    # Add some liquidity levels
    for i in range(10):
        high = base_price + 100 + i * 50
        low = base_price - 100 - i * 50
        detector.update_liquidity_levels('BTC', high, low, 1000, 
                                        now - timedelta(minutes=i))
    
    # Simulate stop hunt scenario
    # Price sweeps above recent high then rejects
    hunt_price = base_price + 650  # Above the highest level
    
    signal = detector.detect_stop_hunt('BTC', hunt_price, now, 5000)
    
    if signal:
        print(f"\nStop Hunt Detected!")
        print(f"Type: {signal.hunt_type.value}")
        print(f"Trigger Price: ${signal.trigger_price:,.2f}")
        print(f"Target Level: ${signal.target_level:,.2f}")
        print(f"Sweep Distance: {signal.sweep_distance:.3%}")
        print(f"Rejection Strength: {signal.rejection_strength:.2f}")
        print(f"Fade Direction: {signal.fade_direction}")
        print(f"Entry: ${signal.entry_price:,.2f}")
        print(f"Stop: ${signal.stop_loss:,.2f}")
        print(f"Confidence: {signal.confidence:.2f}")
    else:
        print("No stop hunt detected")
    
    # Show liquidity zones
    zones = detector.get_liquidity_zones('BTC')
    print(f"\nLiquidity Zones for BTC:")
    print(f"Highs: {[h['price'] for h in zones['highs']]}")
    print(f"Lows: {[l['price'] for l in zones['lows']]}")
