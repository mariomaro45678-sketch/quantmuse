"""
Volume Delta & Footprint Analyzer
==================================
Advanced volume delta analysis with footprint chart interpretation.

Features:
- Volume delta calculation (aggressive buy vs sell)
- Cumulative Volume Delta (CVD) tracking
- Delta divergence detection
- Footprint pattern recognition
- Absorption and exhaustion detection
- Tick-level momentum analysis
"""

import logging
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
from enum import Enum

logger = logging.getLogger(__name__)


class DeltaDirection(Enum):
    POSITIVE = 1
    NEGATIVE = -1
    NEUTRAL = 0


@dataclass
class TickData:
    """Individual tick/trade data."""
    timestamp: datetime
    price: float
    size: float
    side: str  # 'buy' or 'sell'
    aggressor: str  # 'buyer' or 'seller' - who initiated


@dataclass
class FootprintCandle:
    """Footprint candle with bid/ask volume at each price level."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    
    # Volume by price level
    bid_volumes: Dict[float, float] = None  # price -> volume
    ask_volumes: Dict[float, float] = None  # price -> volume
    
    # Aggregates
    total_bid_vol: float = 0.0
    total_ask_vol: float = 0.0
    delta: float = 0.0
    
    # POC (Point of Control)
    poc_price: float = 0.0
    poc_volume: float = 0.0
    
    def __post_init__(self):
        if self.bid_volumes is None:
            self.bid_volumes = {}
        if self.ask_volumes is None:
            self.ask_volumes = {}


@dataclass
class DeltaMetrics:
    """Volume delta metrics."""
    # Time-windowed deltas
    delta_1m: float = 0.0
    delta_5m: float = 0.0
    delta_15m: float = 0.0
    
    # Cumulative
    cumulative_delta: float = 0.0
    cumulative_delta_ma: float = 0.0
    
    # Ratios
    buy_ratio: float = 0.5
    sell_ratio: float = 0.5
    delta_imbalance: float = 0.0
    
    # Momentum
    delta_momentum: float = 0.0
    delta_acceleration: float = 0.0
    
    # Divergence
    delta_divergence: bool = False
    divergence_strength: float = 0.0
    
    # Quality
    is_valid: bool = True


@dataclass
class FootprintMetrics:
    """Footprint analysis metrics."""
    # Current candle
    current_candle: Optional[FootprintCandle] = None
    
    # Patterns
    absorption_detected: bool = False
    exhaustion_detected: bool = False
    initiation_detected: bool = False
    
    # Volume profile
    high_volume_nodes: List[Tuple[float, float]] = None  # (price, volume)
    low_volume_nodes: List[Tuple[float, float]] = None
    value_area_high: float = 0.0
    value_area_low: float = 0.0
    
    # Imbalances
    imbalances: List[Dict] = None  # List of imbalance records
    stacked_imbalances: bool = False
    
    def __post_init__(self):
        if self.high_volume_nodes is None:
            self.high_volume_nodes = []
        if self.low_volume_nodes is None:
            self.low_volume_nodes = []
        if self.imbalances is None:
            self.imbalances = []


class VolumeDeltaAnalyzer:
    """
    Professional volume delta analyzer for scalping.
    
    Tracks aggressive buying/selling and identifies:
    - Delta divergences (leading indicator)
    - Absorption zones (resistance/support)
    - Exhaustion patterns (reversal signals)
    """
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        
        # Time windows (in minutes)
        self.windows = [1, 5, 15]
        
        # Thresholds
        self.min_delta_threshold = self.config.get('min_delta_threshold', 1000)
        self.divergence_threshold = self.config.get('divergence_threshold', 0.3)
        self.absorption_volume_mult = self.config.get('absorption_volume_mult', 2.0)
        self.absorption_range_mult = self.config.get('absorption_range_mult', 0.5)
        
        # State
        self.tick_buffer: deque = deque(maxlen=5000)
        self.candles_1m: deque = deque(maxlen=100)
        self.cumulative_delta: float = 0.0
        self.delta_history: deque = deque(maxlen=50)
        
    def process_tick(self, tick: TickData):
        """
        Process a single tick/trade.
        
        Args:
            tick: TickData object
        """
        self.tick_buffer.append(tick)
        
        # Update cumulative delta
        if tick.aggressor == 'buyer':
            self.cumulative_delta += tick.size
        else:
            self.cumulative_delta -= tick.size
    
    def calculate_delta_metrics(self) -> DeltaMetrics:
        """
        Calculate comprehensive delta metrics.
        
        Returns:
            DeltaMetrics object
        """
        metrics = DeltaMetrics()
        
        try:
            now = datetime.now()
            ticks = list(self.tick_buffer)
            
            if not ticks:
                return metrics
            
            # Calculate windowed deltas
            buy_vol_1m = 0.0
            sell_vol_1m = 0.0

            for window in self.windows:
                cutoff = now - timedelta(minutes=window)
                window_ticks = [t for t in ticks if t.timestamp >= cutoff]

                if window_ticks:
                    buy_vol = sum(t.size for t in window_ticks if t.aggressor == 'buyer')
                    sell_vol = sum(t.size for t in window_ticks if t.aggressor == 'seller')
                    delta = buy_vol - sell_vol

                    if window == 1:
                        metrics.delta_1m = delta
                        buy_vol_1m = buy_vol
                        sell_vol_1m = sell_vol
                    elif window == 5:
                        metrics.delta_5m = delta
                    elif window == 15:
                        metrics.delta_15m = delta

            # Cumulative delta
            metrics.cumulative_delta = self.cumulative_delta

            # Calculate ratios using actual buy/sell volumes
            total_vol = buy_vol_1m + sell_vol_1m
            if total_vol > 0:
                metrics.buy_ratio = buy_vol_1m / total_vol
                metrics.sell_ratio = sell_vol_1m / total_vol
                metrics.delta_imbalance = abs(metrics.buy_ratio - 0.5) * 2
            
            # Delta momentum (change rate)
            if len(self.delta_history) >= 5:
                recent = list(self.delta_history)[-5:]
                metrics.delta_momentum = (recent[-1] - recent[0]) / len(recent) if len(recent) > 0 else 0
            
            # Store history
            self.delta_history.append(metrics.delta_1m)
            
            # Detect divergence
            metrics = self._detect_divergence(metrics, ticks)
            
        except Exception as e:
            logger.error(f"Error calculating delta metrics: {e}")
            metrics.is_valid = False
        
        return metrics
    
    def _detect_divergence(self, metrics: DeltaMetrics, 
                          ticks: List[TickData]) -> DeltaMetrics:
        """
        Detect delta divergence from price.
        
        Bullish divergence: Price down, delta up
        Bearish divergence: Price up, delta down
        """
        if len(ticks) < 20:
            return metrics
        
        try:
            # Calculate price change
            recent_ticks = ticks[-20:]
            price_start = recent_ticks[0].price
            price_end = recent_ticks[-1].price
            price_change = (price_end - price_start) / price_start
            
            # Calculate delta change
            delta_start = sum(t.size if t.aggressor == 'buyer' else -t.size 
                            for t in recent_ticks[:10])
            delta_end = sum(t.size if t.aggressor == 'buyer' else -t.size 
                          for t in recent_ticks[-10:])
            delta_change = delta_end - delta_start
            
            # Detect divergence
            if price_change > self.divergence_threshold * 0.01 and delta_change < 0:
                # Price up, delta down = bearish divergence
                metrics.delta_divergence = True
                metrics.divergence_strength = abs(price_change * 100)
            elif price_change < -self.divergence_threshold * 0.01 and delta_change > 0:
                # Price down, delta up = bullish divergence
                metrics.delta_divergence = True
                metrics.divergence_strength = abs(price_change * 100)
        
        except Exception as e:
            logger.warning(f"Divergence detection error: {e}")
        
        return metrics
    
    def build_footprint_candle(self, ticks: List[TickData], 
                               open_price: float) -> FootprintCandle:
        """
        Build footprint candle from ticks.
        
        Args:
            ticks: List of tick data
            open_price: Opening price
        
        Returns:
            FootprintCandle
        """
        if not ticks:
            return None
        
        candle = FootprintCandle(
            timestamp=ticks[0].timestamp,
            open=open_price,
            high=max(t.price for t in ticks),
            low=min(t.price for t in ticks),
            close=ticks[-1].price
        )
        
        # Aggregate volumes by price
        price_volumes = {}
        for tick in ticks:
            # Round price to create levels
            price_level = round(tick.price, 2)
            
            if price_level not in price_volumes:
                price_volumes[price_level] = {'bid': 0, 'ask': 0}
            
            if tick.aggressor == 'buyer':
                price_volumes[price_level]['ask'] += tick.size
            else:
                price_volumes[price_level]['bid'] += tick.size
        
        # Populate candle
        max_vol = 0
        for price, vols in price_volumes.items():
            candle.bid_volumes[price] = vols['bid']
            candle.ask_volumes[price] = vols['ask']
            candle.total_bid_vol += vols['bid']
            candle.total_ask_vol += vols['ask']
            
            total_at_price = vols['bid'] + vols['ask']
            if total_at_price > max_vol:
                max_vol = total_at_price
                candle.poc_price = price
                candle.poc_volume = total_at_price
        
        candle.delta = candle.total_ask_vol - candle.total_bid_vol
        
        return candle
    
    def analyze_footprint(self, candle: FootprintCandle) -> FootprintMetrics:
        """
        Analyze footprint candle for patterns.
        
        Returns:
            FootprintMetrics
        """
        metrics = FootprintMetrics(current_candle=candle)
        
        if not candle:
            return metrics
        
        try:
            # Detect absorption
            metrics = self._detect_absorption(metrics, candle)
            
            # Detect exhaustion
            metrics = self._detect_exhaustion(metrics, candle)
            
            # Detect initiation
            metrics = self._detect_initiation(metrics, candle)
            
            # Find volume nodes
            metrics = self._find_volume_nodes(metrics, candle)
            
            # Detect imbalances
            metrics = self._detect_imbalances(metrics, candle)
            
        except Exception as e:
            logger.error(f"Footprint analysis error: {e}")
        
        return metrics
    
    def _detect_absorption(self, metrics: FootprintMetrics, 
                          candle: FootprintCandle) -> FootprintMetrics:
        """
        Detect absorption pattern.
        
        Absorption: High volume but minimal price movement.
        Indicates strong passive liquidity absorbing aggressive orders.
        """
        try:
            # Check if we have historical data
            if len(self.candles_1m) < 10:
                return metrics
            
            hist_candles = list(self.candles_1m)[-10:]
            avg_volume = np.mean([c.total_bid_vol + c.total_ask_vol for c in hist_candles])
            avg_range = np.mean([c.high - c.low for c in hist_candles])
            
            current_volume = candle.total_bid_vol + candle.total_ask_vol
            current_range = candle.high - candle.low
            
            # High volume + low range = absorption
            if (current_volume > avg_volume * self.absorption_volume_mult and
                current_range < avg_range * self.absorption_range_mult):
                metrics.absorption_detected = True
                logger.info(f"Absorption detected: Vol={current_volume:.0f} "
                           f"(avg={avg_volume:.0f}), Range={current_range:.2f}")
        
        except Exception as e:
            logger.warning(f"Absorption detection error: {e}")
        
        return metrics
    
    def _detect_exhaustion(self, metrics: FootprintMetrics,
                          candle: FootprintCandle) -> FootprintMetrics:
        """
        Detect exhaustion pattern.
        
        Exhaustion: Large delta but price not following through.
        Indicates aggressive traders are running out of ammunition.
        """
        try:
            if len(self.candles_1m) < 5:
                return metrics
            
            hist_candles = list(self.candles_1m)[-5:]
            avg_delta = np.mean([abs(c.delta) for c in hist_candles])
            
            current_delta = abs(candle.delta)
            
            # Large delta
            if current_delta > avg_delta * 3:
                # Check if price moved in opposite direction of delta
                price_direction = 1 if candle.close > candle.open else -1
                delta_direction = 1 if candle.delta > 0 else -1
                
                if price_direction != delta_direction:
                    metrics.exhaustion_detected = True
                    logger.info(f"Exhaustion detected: Delta={candle.delta:.0f} "
                               f"vs Price direction={price_direction}")
        
        except Exception as e:
            logger.warning(f"Exhaustion detection error: {e}")
        
        return metrics
    
    def _detect_initiation(self, metrics: FootprintMetrics,
                          candle: FootprintCandle) -> FootprintMetrics:
        """
        Detect initiation pattern.
        
        Initiation: Strong delta in direction of price move.
        Indicates start of aggressive move.
        """
        try:
            price_direction = 1 if candle.close > candle.open else -1
            delta_direction = 1 if candle.delta > 0 else -1
            
            # Delta aligns with price
            if price_direction == delta_direction:
                # Check for above-average delta
                if len(self.candles_1m) >= 5:
                    hist_candles = list(self.candles_1m)[-5:]
                    avg_delta = np.mean([abs(c.delta) for c in hist_candles])
                    
                    if abs(candle.delta) > avg_delta * 1.5:
                        metrics.initiation_detected = True
        
        except Exception as e:
            logger.warning(f"Initiation detection error: {e}")
        
        return metrics
    
    def _find_volume_nodes(self, metrics: FootprintMetrics,
                          candle: FootprintCandle) -> FootprintMetrics:
        """Find high and low volume nodes."""
        try:
            all_volumes = []
            for price in set(list(candle.bid_volumes.keys()) + list(candle.ask_volumes.keys())):
                bid_vol = candle.bid_volumes.get(price, 0)
                ask_vol = candle.ask_volumes.get(price, 0)
                total = bid_vol + ask_vol
                all_volumes.append((price, total))
            
            if not all_volumes:
                return metrics
            
            # Sort by volume
            all_volumes.sort(key=lambda x: x[1], reverse=True)
            
            # Top 20% are high volume nodes
            hvn_count = max(1, len(all_volumes) // 5)
            metrics.high_volume_nodes = all_volumes[:hvn_count]
            
            # Bottom 20% are low volume nodes
            lvn_count = max(1, len(all_volumes) // 5)
            metrics.low_volume_nodes = all_volumes[-lvn_count:]
            
            # Calculate value area (70% of volume)
            total_vol = sum(v for _, v in all_volumes)
            running_vol = 0
            value_prices = []
            
            for price, vol in sorted(all_volumes, key=lambda x: x[0]):
                running_vol += vol
                value_prices.append(price)
                if running_vol >= total_vol * 0.7:
                    break
            
            if value_prices:
                metrics.value_area_low = min(value_prices)
                metrics.value_area_high = max(value_prices)
        
        except Exception as e:
            logger.warning(f"Volume node detection error: {e}")
        
        return metrics
    
    def _detect_imbalances(self, metrics: FootprintMetrics,
                          candle: FootprintCandle) -> FootprintMetrics:
        """
        Detect volume imbalances at price levels.
        
        Imbalance: One side dominates at a price level (3:1 or more).
        """
        try:
            imbalances = []
            
            all_prices = sorted(set(list(candle.bid_volumes.keys()) + 
                                   list(candle.ask_volumes.keys())))
            
            for price in all_prices:
                bid_vol = candle.bid_volumes.get(price, 0)
                ask_vol = candle.ask_volumes.get(price, 0)
                
                if bid_vol == 0 or ask_vol == 0:
                    continue
                
                ratio = max(bid_vol, ask_vol) / min(bid_vol, ask_vol)
                
                if ratio >= 3.0:  # 3:1 imbalance
                    imbalances.append({
                        'price': price,
                        'ratio': ratio,
                        'direction': 'sell' if bid_vol > ask_vol else 'buy',
                        'bid_vol': bid_vol,
                        'ask_vol': ask_vol
                    })
            
            metrics.imbalances = imbalances
            
            # Check for stacked imbalances (3+ consecutive levels)
            if len(imbalances) >= 3:
                prices = sorted([i['price'] for i in imbalances])
                consecutive = 1
                for i in range(1, len(prices)):
                    if abs(prices[i] - prices[i-1]) < 0.5:  # Adjacent levels
                        consecutive += 1
                        if consecutive >= 3:
                            metrics.stacked_imbalances = True
                            break
                    else:
                        consecutive = 1
        
        except Exception as e:
            logger.warning(f"Imbalance detection error: {e}")
        
        return metrics
    
    def get_signal_quality(self, delta_metrics: DeltaMetrics,
                          footprint_metrics: FootprintMetrics) -> Tuple[str, float]:
        """
        Get overall signal quality assessment.
        
        Returns:
            (quality, confidence) - quality in ['strong', 'moderate', 'weak']
        """
        score = 0.0
        
        # Delta strength
        if abs(delta_metrics.delta_1m) > 5000:
            score += 0.25
        elif abs(delta_metrics.delta_1m) > 2000:
            score += 0.15
        
        # Delta imbalance
        if delta_metrics.delta_imbalance > 0.6:
            score += 0.20
        
        # Divergence (reduce score)
        if delta_metrics.delta_divergence:
            score -= 0.30
        
        # Absorption (increase score for reversal plays)
        if footprint_metrics.absorption_detected:
            score += 0.15
        
        # Stacked imbalances
        if footprint_metrics.stacked_imbalances:
            score += 0.20
        
        # Determine quality
        if score >= 0.7:
            return "strong", min(0.95, score)
        elif score >= 0.4:
            return "moderate", min(0.85, score)
        else:
            return "weak", max(0.0, score)


# Helper functions
def create_tick_from_trade(trade: Dict) -> TickData:
    """Create TickData from exchange trade data."""
    return TickData(
        timestamp=trade.get('timestamp', datetime.now()),
        price=float(trade['price']),
        size=float(trade['size']),
        side=trade.get('side', 'buy'),
        aggressor=trade.get('aggressor', 'buyer')
    )


if __name__ == "__main__":
    # Test the analyzer
    analyzer = VolumeDeltaAnalyzer()
    
    # Simulate ticks
    import random
    base_price = 50000
    
    for i in range(100):
        # Simulate alternating buying/selling pressure
        if i < 30:
            aggressor = 'buyer'  # Buying pressure
        elif i < 60:
            aggressor = 'seller'  # Selling pressure
        else:
            aggressor = random.choice(['buyer', 'seller'])
        
        price = base_price + random.uniform(-50, 50)
        size = random.uniform(0.1, 2.0)
        
        tick = TickData(
            timestamp=datetime.now(),
            price=price,
            size=size,
            side='buy' if aggressor == 'buyer' else 'sell',
            aggressor=aggressor
        )
        
        analyzer.process_tick(tick)
    
    # Calculate metrics
    delta_metrics = analyzer.calculate_delta_metrics()
    
    print("Volume Delta Analysis")
    print(f"Delta 1m: {delta_metrics.delta_1m:.2f}")
    print(f"Delta 5m: {delta_metrics.delta_5m:.2f}")
    print(f"Cumulative Delta: {delta_metrics.cumulative_delta:.2f}")
    print(f"Buy Ratio: {delta_metrics.buy_ratio:.2%}")
    print(f"Divergence: {delta_metrics.delta_divergence}")
    print(f"Divergence Strength: {delta_metrics.divergence_strength:.2f}")
