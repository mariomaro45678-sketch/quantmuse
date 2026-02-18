"""
Order Book Microstructure Analyzer
===================================
Advanced order book analysis with smart filtering and microstructure detection.

Features:
- Multi-level order book imbalance (L1-L10)
- Smart filtering (removes transient/spoofing orders)
- Persistence weighting
- Spread analysis
- Liquidity depth mapping
- Queue position analysis
"""

import logging
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class OrderBookLevel:
    """Single order book level data."""
    price: float
    size: float
    age_ms: float = 0.0
    order_count: int = 1
    update_count: int = 0
    
    
@dataclass
class OrderBookSnapshot:
    """Complete order book snapshot."""
    symbol: str
    timestamp: datetime
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    
    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0
    
    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0
    
    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2
        return 0
    
    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid
    
    @property
    def spread_pct(self) -> float:
        mid = self.mid_price
        return (self.spread / mid) * 100 if mid > 0 else 0


@dataclass
class MicrostructureMetrics:
    """Calculated microstructure metrics."""
    # Imbalance metrics
    obi_l1: float = 0.0  # Level 1 imbalance
    obi_l5: float = 0.0  # Level 5 imbalance
    obi_l10: float = 0.0  # Level 10 imbalance
    obi_weighted: float = 0.0  # Persistence-weighted
    obi_filtered: float = 0.0  # Filtered (no transient)
    
    # Liquidity metrics
    depth_bid_usd: float = 0.0
    depth_ask_usd: float = 0.0
    depth_ratio: float = 1.0
    liquidity_score: float = 0.0
    
    # Spread metrics
    spread_pct: float = 0.0
    spread_zscore: float = 0.0
    spread_condition: str = "normal"  # normal, wide, extreme
    
    # Microstructure patterns
    bid_wall_detected: bool = False
    ask_wall_detected: bool = False
    wall_size_usd: float = 0.0
    
    # Queue position (for execution planning)
    avg_queue_position_bid: float = 0.0
    avg_queue_position_ask: float = 0.0
    
    # Quality flags
    is_valid: bool = True
    rejection_reason: str = ""


class OrderBookMicrostructureAnalyzer:
    """
    Professional-grade order book microstructure analyzer.
    
    Filters noise, detects genuine liquidity, and calculates
    reliable imbalance signals.
    """
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        
        # Thresholds
        self.transient_threshold_ms = self.config.get('transient_threshold_ms', 100)
        self.short_lived_threshold_ms = self.config.get('short_lived_threshold_ms', 500)
        self.persistent_threshold_ms = self.config.get('persistent_threshold_ms', 2000)
        
        # Spread thresholds
        self.spread_normal = self.config.get('spread_normal_pct', 0.10)
        self.spread_wide = self.config.get('spread_wide_pct', 0.20)
        self.spread_extreme = self.config.get('spread_extreme_pct', 0.50)
        
        # Liquidity requirements
        self.min_depth_usd = self.config.get('min_depth_usd', 1_000_000)
        self.wall_threshold = self.config.get('wall_threshold', 3.0)  # 3x avg
        
        # State
        self.spread_history: deque = deque(maxlen=100)
        self.obi_history: deque = deque(maxlen=50)
        
    def analyze(self, snapshot: OrderBookSnapshot) -> MicrostructureMetrics:
        """
        Analyze order book snapshot and return microstructure metrics.
        """
        metrics = MicrostructureMetrics()
        
        try:
            # Validate snapshot
            if not self._validate_snapshot(snapshot):
                metrics.is_valid = False
                metrics.rejection_reason = "Invalid snapshot"
                return metrics
            
            # Calculate imbalances at different levels
            metrics = self._calculate_imbalances(metrics, snapshot)
            
            # Calculate liquidity metrics
            metrics = self._calculate_liquidity(metrics, snapshot)
            
            # Analyze spread
            metrics = self._analyze_spread(metrics, snapshot)
            
            # Detect walls and significant levels
            metrics = self._detect_walls(metrics, snapshot)
            
            # Queue position analysis
            metrics = self._analyze_queue_position(metrics, snapshot)
            
            # Store history
            self.spread_history.append(metrics.spread_pct)
            self.obi_history.append(metrics.obi_weighted)
            
            # Final validation
            metrics = self._final_validation(metrics)
            
        except Exception as e:
            logger.error(f"Error analyzing order book: {e}")
            metrics.is_valid = False
            metrics.rejection_reason = f"Analysis error: {str(e)}"
        
        return metrics
    
    def _validate_snapshot(self, snapshot: OrderBookSnapshot) -> bool:
        """Validate order book snapshot."""
        if not snapshot.bids or not snapshot.asks:
            return False
        if len(snapshot.bids) < 3 or len(snapshot.asks) < 3:
            return False
        if snapshot.best_bid <= 0 or snapshot.best_ask <= 0:
            return False
        if snapshot.best_bid >= snapshot.best_ask:
            return False
        return True
    
    def _calculate_imbalances(self, metrics: MicrostructureMetrics, 
                             snapshot: OrderBookSnapshot) -> MicrostructureMetrics:
        """Calculate order book imbalances at multiple levels."""
        bids = snapshot.bids
        asks = snapshot.asks
        
        # Level 1 imbalance (best bid/ask only)
        bid_vol_1 = bids[0].size
        ask_vol_1 = asks[0].size
        total_1 = bid_vol_1 + ask_vol_1
        metrics.obi_l1 = (bid_vol_1 - ask_vol_1) / total_1 if total_1 > 0 else 0
        
        # Level 5 imbalance
        bid_vol_5 = sum(b.size for b in bids[:5])
        ask_vol_5 = sum(a.size for a in asks[:5])
        total_5 = bid_vol_5 + ask_vol_5
        metrics.obi_l5 = (bid_vol_5 - ask_vol_5) / total_5 if total_5 > 0 else 0
        
        # Level 10 imbalance
        bid_vol_10 = sum(b.size for b in bids[:10])
        ask_vol_10 = sum(a.size for a in asks[:10])
        total_10 = bid_vol_10 + ask_vol_10
        metrics.obi_l10 = (bid_vol_10 - ask_vol_10) / total_10 if total_10 > 0 else 0
        
        # Weighted imbalance (by persistence)
        bid_vol_w = sum(b.size * self._get_persistence_weight(b) for b in bids[:10])
        ask_vol_w = sum(a.size * self._get_persistence_weight(a) for a in asks[:10])
        total_w = bid_vol_w + ask_vol_w
        metrics.obi_weighted = (bid_vol_w - ask_vol_w) / total_w if total_w > 0 else 0
        
        # Filtered imbalance (removes transient orders)
        bid_vol_f = sum(b.size for b in bids[:10] if b.age_ms > self.transient_threshold_ms)
        ask_vol_f = sum(a.size for a in asks[:10] if a.age_ms > self.transient_threshold_ms)
        total_f = bid_vol_f + ask_vol_f
        metrics.obi_filtered = (bid_vol_f - ask_vol_f) / total_f if total_f > 0 else 0
        
        return metrics
    
    def _get_persistence_weight(self, level: OrderBookLevel) -> float:
        """
        Calculate persistence weight for an order book level.
        
        Older orders are more likely to represent genuine interest.
        """
        age = level.age_ms
        
        if age < self.transient_threshold_ms:
            # Likely HFT/spoofing
            return 0.1
        elif age < self.short_lived_threshold_ms:
            # Short-lived, may be tactical
            return 0.4
        elif age < self.persistent_threshold_ms:
            # Medium persistence
            return 0.7
        else:
            # Persistent, likely genuine
            return 1.0
    
    def _calculate_liquidity(self, metrics: MicrostructureMetrics,
                            snapshot: OrderBookSnapshot) -> MicrostructureMetrics:
        """Calculate liquidity metrics."""
        mid = snapshot.mid_price
        
        # Calculate USD depth
        metrics.depth_bid_usd = sum(b.size * b.price for b in snapshot.bids[:10])
        metrics.depth_ask_usd = sum(a.size * a.price for a in snapshot.asks[:10])
        
        # Depth ratio (bid/ask)
        if metrics.depth_ask_usd > 0:
            metrics.depth_ratio = metrics.depth_bid_usd / metrics.depth_ask_usd
        
        # Liquidity score (0-1)
        min_depth = min(metrics.depth_bid_usd, metrics.depth_ask_usd)
        metrics.liquidity_score = min(1.0, min_depth / self.min_depth_usd)
        
        return metrics
    
    def _analyze_spread(self, metrics: MicrostructureMetrics,
                       snapshot: OrderBookSnapshot) -> MicrostructureMetrics:
        """Analyze spread conditions."""
        metrics.spread_pct = snapshot.spread_pct
        
        # Calculate Z-score if we have history
        if len(self.spread_history) >= 20:
            hist_mean = np.mean(self.spread_history)
            hist_std = np.std(self.spread_history)
            if hist_std > 0:
                metrics.spread_zscore = (metrics.spread_pct - hist_mean) / hist_std
        
        # Classify spread condition
        if metrics.spread_pct > self.spread_extreme:
            metrics.spread_condition = "extreme"
        elif metrics.spread_pct > self.spread_wide:
            metrics.spread_condition = "wide"
        elif metrics.spread_pct > self.spread_normal:
            metrics.spread_condition = "elevated"
        else:
            metrics.spread_condition = "normal"
        
        return metrics
    
    def _detect_walls(self, metrics: MicrostructureMetrics,
                     snapshot: OrderBookSnapshot) -> MicrostructureMetrics:
        """Detect liquidity walls in order book."""
        # Calculate average order size
        all_sizes = [b.size for b in snapshot.bids[:10]] + [a.size for a in snapshot.asks[:10]]
        avg_size = np.mean(all_sizes) if all_sizes else 0
        
        if avg_size == 0:
            return metrics
        
        # Check for bid walls
        for i, bid in enumerate(snapshot.bids[:5]):
            if bid.size > avg_size * self.wall_threshold:
                metrics.bid_wall_detected = True
                metrics.wall_size_usd = max(metrics.wall_size_usd, bid.size * bid.price)
                break
        
        # Check for ask walls
        for i, ask in enumerate(snapshot.asks[:5]):
            if ask.size > avg_size * self.wall_threshold:
                metrics.ask_wall_detected = True
                metrics.wall_size_usd = max(metrics.wall_size_usd, ask.size * ask.price)
                break
        
        return metrics
    
    def _analyze_queue_position(self, metrics: MicrostructureMetrics,
                               snapshot: OrderBookSnapshot) -> MicrostructureMetrics:
        """
        Estimate queue position for marketable orders.
        
        Lower queue position = faster fill
        """
        # Simplified queue analysis
        # In reality, would need actual order queue data
        
        bid_sizes = [b.size for b in snapshot.bids[:5]]
        ask_sizes = [a.size for a in snapshot.asks[:5]]
        
        if bid_sizes:
            # Average position based on size distribution
            metrics.avg_queue_position_bid = np.mean(bid_sizes[:3])
        
        if ask_sizes:
            metrics.avg_queue_position_ask = np.mean(ask_sizes[:3])
        
        return metrics
    
    def _final_validation(self, metrics: MicrostructureMetrics) -> MicrostructureMetrics:
        """Final validation of metrics."""
        # Check spread
        if metrics.spread_condition == "extreme":
            metrics.is_valid = False
            metrics.rejection_reason = f"Extreme spread: {metrics.spread_pct:.2f}%"
            return metrics
        
        # Check liquidity
        if metrics.liquidity_score < 0.5:
            metrics.is_valid = False
            metrics.rejection_reason = f"Insufficient liquidity: {metrics.liquidity_score:.2f}"
            return metrics
        
        # Check for conflicting signals
        if abs(metrics.obi_l1 - metrics.obi_weighted) > 0.5:
            logger.warning(f"Large OBI discrepancy: L1={metrics.obi_l1:.2f}, "
                          f"Weighted={metrics.obi_weighted:.2f}")
        
        return metrics
    
    def get_imbalance_pressure(self, metrics: MicrostructureMetrics) -> str:
        """Get imbalance pressure direction."""
        if metrics.obi_filtered > 0.4:
            return "bullish"
        elif metrics.obi_filtered < -0.4:
            return "bearish"
        else:
            return "neutral"
    
    def should_trade(self, metrics: MicrostructureMetrics, direction: str) -> Tuple[bool, str]:
        """
        Determine if trading conditions are favorable.
        
        Args:
            metrics: MicrostructureMetrics
            direction: "long" or "short"
        
        Returns:
            (should_trade, reason)
        """
        if not metrics.is_valid:
            return False, metrics.rejection_reason
        
        # Check spread
        if metrics.spread_pct > self.spread_wide:
            return False, f"Wide spread: {metrics.spread_pct:.2f}%"
        
        # Check imbalance alignment
        pressure = self.get_imbalance_pressure(metrics)
        if direction == "long" and pressure == "bearish":
            return False, "OB bearish pressure"
        if direction == "short" and pressure == "bullish":
            return False, "OB bullish pressure"
        
        # Check for walls blocking trade
        if direction == "long" and metrics.ask_wall_detected:
            return False, "Ask wall blocking"
        if direction == "short" and metrics.bid_wall_detected:
            return False, "Bid wall blocking"
        
        return True, "OK"


# Convenience function
def create_order_book_snapshot(symbol: str, raw_bids: List[Dict], 
                               raw_asks: List[Dict]) -> OrderBookSnapshot:
    """
    Create OrderBookSnapshot from raw exchange data.
    
    Args:
        symbol: Trading symbol
        raw_bids: List of [price, size] or dict with price/size
        raw_asks: List of [price, size] or dict with price/size
    """
    bids = []
    asks = []
    
    for b in raw_bids:
        if isinstance(b, (list, tuple)):
            bids.append(OrderBookLevel(price=float(b[0]), size=float(b[1])))
        else:
            bids.append(OrderBookLevel(
                price=float(b['price']),
                size=float(b['size']),
                age_ms=b.get('age_ms', 0),
                order_count=b.get('count', 1)
            ))
    
    for a in raw_asks:
        if isinstance(a, (list, tuple)):
            asks.append(OrderBookLevel(price=float(a[0]), size=float(a[1])))
        else:
            asks.append(OrderBookLevel(
                price=float(a['price']),
                size=float(a['size']),
                age_ms=a.get('age_ms', 0),
                order_count=a.get('count', 1)
            ))
    
    return OrderBookSnapshot(
        symbol=symbol,
        timestamp=datetime.now(),
        bids=bids,
        asks=asks
    )


if __name__ == "__main__":
    # Test the analyzer
    analyzer = OrderBookMicrostructureAnalyzer()
    
    # Create test snapshot
    raw_bids = [
        {'price': 50000, 'size': 2.5, 'age_ms': 5000},
        {'price': 49995, 'size': 5.0, 'age_ms': 10000},
        {'price': 49990, 'size': 8.0, 'age_ms': 50},  # Transient
        {'price': 49985, 'size': 12.0, 'age_ms': 15000},
    ]
    
    raw_asks = [
        {'price': 50010, 'size': 3.0, 'age_ms': 8000},
        {'price': 50015, 'size': 6.0, 'age_ms': 12000},
        {'price': 50020, 'size': 4.0, 'age_ms': 30},  # Transient
        {'price': 50025, 'size': 10.0, 'age_ms': 20000},
    ]
    
    snapshot = create_order_book_snapshot('BTC', raw_bids, raw_asks)
    metrics = analyzer.analyze(snapshot)
    
    print(f"Order Book Analysis for {snapshot.symbol}")
    print(f"Mid Price: ${snapshot.mid_price:,.2f}")
    print(f"Spread: {metrics.spread_pct:.3f}% ({metrics.spread_condition})")
    print(f"OBI L1: {metrics.obi_l1:.3f}")
    print(f"OBI Filtered: {metrics.obi_filtered:.3f}")
    print(f"Liquidity Score: {metrics.liquidity_score:.2f}")
    print(f"Bid Wall: {metrics.bid_wall_detected}, Ask Wall: {metrics.ask_wall_detected}")
    print(f"Valid: {metrics.is_valid} ({metrics.rejection_reason if not metrics.is_valid else 'OK'})")
