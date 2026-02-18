"""
Order Book Imbalance Factor

Calculates order book imbalance from Level-2 data to detect:
- Buying pressure (more bid volume = bullish)
- Selling pressure (more ask volume = bearish)

Formula: imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)

Interpretation:
- imbalance > +0.3: Bullish pressure (buyers dominating)
- imbalance < -0.3: Bearish pressure (sellers dominating)
- Between -0.3 and +0.3: Neutral/balanced

Integration with strategies:
- Boost confidence by 0.10 when imbalance agrees with direction
- Reduce confidence by 0.15 when imbalance conflicts with direction
"""

import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class OrderBookImbalance:
    """Order book imbalance state for a symbol."""
    symbol: str
    imbalance: float  # -1 to +1, positive = bullish
    bid_volume: float  # Total volume on bid side (top N levels)
    ask_volume: float  # Total volume on ask side (top N levels)
    bid_levels: int  # Number of bid levels used
    ask_levels: int  # Number of ask levels used
    spread_pct: float  # Bid-ask spread as percentage
    pressure: str  # "bullish", "bearish", or "neutral"
    calculated_at: datetime

    def agrees_with(self, direction: str) -> bool:
        """Check if imbalance agrees with trade direction."""
        if direction == 'long':
            return self.pressure == 'bullish'
        elif direction == 'short':
            return self.pressure == 'bearish'
        return True  # Neutral agrees with flat

    def conflicts_with(self, direction: str) -> bool:
        """Check if imbalance conflicts with trade direction."""
        if direction == 'long':
            return self.pressure == 'bearish'
        elif direction == 'short':
            return self.pressure == 'bullish'
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "imbalance": self.imbalance,
            "bid_volume": self.bid_volume,
            "ask_volume": self.ask_volume,
            "spread_pct": self.spread_pct,
            "pressure": self.pressure,
            "calculated_at": self.calculated_at.isoformat(),
        }


class OrderBookFactors:
    """
    Calculates order book imbalance and related microstructure factors.

    Uses Level-2 order book data to determine:
    1. Buy/sell pressure from volume imbalance
    2. Spread conditions (liquidity indicator)
    3. Confidence adjustments for strategies
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}

        # Imbalance thresholds
        self.bullish_threshold = config.get("bullish_threshold", 0.30)
        self.bearish_threshold = config.get("bearish_threshold", -0.30)

        # Number of levels to consider
        self.levels_to_use = config.get("levels_to_use", 5)

        # Confidence adjustments
        self.confidence_boost = config.get("confidence_boost", 0.10)
        self.confidence_penalty = config.get("confidence_penalty", 0.15)

        # Spread thresholds (as percentage)
        # Hyperliquid stocks typically 0.01-0.05%, metals 0.02-0.10%
        self.spread_normal = config.get("spread_normal", 0.10)  # 0.10%
        self.spread_wide = config.get("spread_wide", 0.25)  # 0.25%
        self.spread_reject = config.get("spread_reject", 0.50)  # 0.50%

        # Cache to avoid repeated API calls
        self._cache: Dict[str, OrderBookImbalance] = {}
        self._cache_ttl_seconds = config.get("cache_ttl_seconds", 10)

        logger.info(f"OrderBookFactors initialized: levels={self.levels_to_use}, "
                   f"bullish>{self.bullish_threshold}, bearish<{self.bearish_threshold}")

    async def calculate(self, symbol: str, fetcher: Any) -> OrderBookImbalance:
        """
        Calculate order book imbalance for a symbol.

        Args:
            symbol: Trading symbol
            fetcher: HyperliquidFetcher instance (or compatible)

        Returns:
            OrderBookImbalance with imbalance metrics
        """
        now = datetime.now()

        # Check cache
        if symbol in self._cache:
            cached = self._cache[symbol]
            age = (now - cached.calculated_at).total_seconds()
            if age < self._cache_ttl_seconds:
                return cached

        try:
            # Fetch L2 order book
            order_book = await fetcher.get_l2_book(symbol)

            if not order_book or not order_book.levels:
                return self._neutral_imbalance(symbol, now)

            bids = order_book.levels[0]  # List of OrderBookEntry
            asks = order_book.levels[1] if len(order_book.levels) > 1 else []

            if not bids or not asks:
                return self._neutral_imbalance(symbol, now)

            # Calculate volumes for top N levels
            bid_volume = sum(entry.sz for entry in bids[:self.levels_to_use])
            ask_volume = sum(entry.sz for entry in asks[:self.levels_to_use])

            # Calculate imbalance
            total_volume = bid_volume + ask_volume
            if total_volume == 0:
                imbalance = 0.0
            else:
                imbalance = (bid_volume - ask_volume) / total_volume

            # Calculate spread
            best_bid = bids[0].px
            best_ask = asks[0].px
            mid_price = (best_bid + best_ask) / 2
            spread_pct = ((best_ask - best_bid) / mid_price) * 100 if mid_price > 0 else 0

            # Determine pressure
            if imbalance > self.bullish_threshold:
                pressure = "bullish"
            elif imbalance < self.bearish_threshold:
                pressure = "bearish"
            else:
                pressure = "neutral"

            result = OrderBookImbalance(
                symbol=symbol,
                imbalance=imbalance,
                bid_volume=bid_volume,
                ask_volume=ask_volume,
                bid_levels=min(len(bids), self.levels_to_use),
                ask_levels=min(len(asks), self.levels_to_use),
                spread_pct=spread_pct,
                pressure=pressure,
                calculated_at=now,
            )

            # Update cache
            self._cache[symbol] = result

            logger.debug(f"[{symbol}] OrderBook: imbalance={imbalance:.3f} ({pressure}) | "
                        f"bid_vol={bid_volume:.2f} ask_vol={ask_volume:.2f} | "
                        f"spread={spread_pct:.3f}%")

            return result

        except Exception as e:
            logger.warning(f"Order book calculation error for {symbol}: {e}")
            return self._neutral_imbalance(symbol, now)

    def adjust_confidence(
        self,
        base_confidence: float,
        direction: str,
        imbalance: OrderBookImbalance
    ) -> tuple[float, str]:
        """
        Adjust signal confidence based on order book imbalance.

        Args:
            base_confidence: Original confidence (0-1)
            direction: Signal direction ('long', 'short', 'flat')
            imbalance: OrderBookImbalance state

        Returns:
            (adjusted_confidence, reason_string)
        """
        if direction == 'flat':
            return base_confidence, ""

        # Check spread conditions first
        spread_adjustment, spread_reason = self._check_spread(imbalance)

        if spread_reason == "reject":
            return 0.0, f"wide spread ({imbalance.spread_pct:.2f}%)"

        # Apply imbalance adjustment
        if imbalance.agrees_with(direction):
            adj = base_confidence + self.confidence_boost
            reason = f"+OB({imbalance.pressure})"
        elif imbalance.conflicts_with(direction):
            adj = base_confidence - self.confidence_penalty
            reason = f"-OB({imbalance.pressure})"
        else:
            adj = base_confidence
            reason = ""

        # Apply spread adjustment
        adj += spread_adjustment
        if spread_reason:
            reason = f"{reason} {spread_reason}".strip()

        # Clamp to valid range
        adj = max(0.0, min(1.0, adj))

        return adj, reason

    def _check_spread(self, imbalance: OrderBookImbalance) -> tuple[float, str]:
        """
        Check spread conditions and return adjustment.

        Returns:
            (confidence_adjustment, reason)
        """
        spread = imbalance.spread_pct

        if spread > self.spread_reject:
            return -1.0, "reject"  # Will be handled specially
        elif spread > self.spread_wide:
            return -0.15, f"wide spread ({spread:.2f}%)"
        elif spread > self.spread_normal:
            return -0.05, f"spread ({spread:.2f}%)"
        else:
            return 0.0, ""

    def get_multi_symbol_imbalance(
        self,
        symbols: List[str],
        fetcher: Any
    ) -> Dict[str, float]:
        """
        Get imbalance values for multiple symbols (sync wrapper).

        Returns dict mapping symbol -> imbalance value.
        """
        import asyncio

        async def _fetch_all():
            results = {}
            for symbol in symbols:
                try:
                    imb = await self.calculate(symbol, fetcher)
                    results[symbol] = imb.imbalance
                except Exception as e:
                    logger.warning(f"Failed to get imbalance for {symbol}: {e}")
                    results[symbol] = 0.0
            return results

        return asyncio.run(_fetch_all())

    def _neutral_imbalance(self, symbol: str, now: datetime) -> OrderBookImbalance:
        """Return neutral imbalance when data is unavailable."""
        return OrderBookImbalance(
            symbol=symbol,
            imbalance=0.0,
            bid_volume=0.0,
            ask_volume=0.0,
            bid_levels=0,
            ask_levels=0,
            spread_pct=0.0,
            pressure="neutral",
            calculated_at=now,
        )

    def clear_cache(self):
        """Clear the imbalance cache."""
        self._cache.clear()

    def get_portfolio_imbalance(
        self,
        imbalances: Dict[str, OrderBookImbalance],
        positions: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
        """
        Calculate portfolio-level order book metrics.

        Args:
            imbalances: Dict of symbol -> OrderBookImbalance
            positions: Optional dict of symbol -> position size (for weighting)

        Returns:
            Dict with aggregate metrics
        """
        if not imbalances:
            return {
                "avg_imbalance": 0.0,
                "bullish_count": 0,
                "bearish_count": 0,
                "neutral_count": 0,
                "avg_spread": 0.0,
            }

        total_imbalance = 0.0
        total_weight = 0.0
        bullish = 0
        bearish = 0
        neutral = 0
        spreads = []

        for symbol, imb in imbalances.items():
            weight = abs(positions.get(symbol, 1.0)) if positions else 1.0
            total_imbalance += imb.imbalance * weight
            total_weight += weight
            spreads.append(imb.spread_pct)

            if imb.pressure == "bullish":
                bullish += 1
            elif imb.pressure == "bearish":
                bearish += 1
            else:
                neutral += 1

        avg_imb = total_imbalance / total_weight if total_weight > 0 else 0.0
        avg_spread = sum(spreads) / len(spreads) if spreads else 0.0

        return {
            "avg_imbalance": avg_imb,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": neutral,
            "avg_spread": avg_spread,
            "symbols_count": len(imbalances),
        }
