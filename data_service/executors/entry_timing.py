"""
Entry Timing Optimization (Phase 3.1)

Improves average entry price by:
1. Placing limit orders at better prices instead of immediate market orders
2. Waiting for pullbacks on sentiment-driven signals
3. Chasing with market orders when price moves away

Entry Strategies:
- Regular signals: Limit 0.1% better, wait 5 min, then market
- Sentiment signals: Wait for 30% pullback, max 30 min, then market
"""

import asyncio
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)


class EntryStrategy(Enum):
    """Entry timing strategy type."""
    IMMEDIATE = "immediate"        # No optimization, market order now
    LIMIT_WAIT = "limit_wait"      # Limit order, wait for fill, chase if needed
    PULLBACK_WAIT = "pullback"     # Wait for pullback before entry


@dataclass
class PendingEntry:
    """Tracks a pending optimized entry."""
    symbol: str
    side: str  # 'buy' or 'sell'
    target_size: float
    signal_price: float  # Price when signal was generated
    limit_price: float   # Our limit order price
    strategy_name: str
    entry_strategy: EntryStrategy
    created_at: datetime = field(default_factory=datetime.now)
    order_id: Optional[int] = None
    status: str = "pending"  # pending, placed, filled, chased, cancelled

    # For pullback tracking
    peak_price: Optional[float] = None  # Highest (long) or lowest (short) since signal
    pullback_target: Optional[float] = None  # Price we're waiting for

    # Timing
    max_wait_seconds: float = 300  # 5 min default
    chase_threshold_pct: float = 0.3  # Chase if price moves 0.3% away


@dataclass
class EntryResult:
    """Result of an entry attempt."""
    success: bool
    order_id: Optional[int] = None
    fill_price: Optional[float] = None
    entry_type: str = "immediate"  # immediate, limit, chased, pullback
    improvement_pct: float = 0.0  # How much better than signal price
    wait_seconds: float = 0.0
    error: Optional[str] = None


class EntryOptimizer:
    """
    Optimizes trade entries for better average prices.

    Works with OrderManager to place and manage orders with timing optimization.
    """

    # Configuration
    DEFAULT_LIMIT_OFFSET_PCT = 0.10  # 0.1% better than current price
    DEFAULT_WAIT_SECONDS = 300       # 5 minutes
    PULLBACK_WAIT_SECONDS = 1800     # 30 minutes for sentiment
    PULLBACK_RETRACEMENT_PCT = 0.30  # Wait for 30% retracement
    CHASE_THRESHOLD_PCT = 0.30       # Chase if price moves 0.3% against us
    CHECK_INTERVAL_SECONDS = 5       # How often to check pending entries

    def __init__(self, order_manager=None, price_fetcher: Optional[Callable] = None):
        """
        Args:
            order_manager: OrderManager instance for placing orders
            price_fetcher: Async callable that returns current price for a symbol
        """
        self.order_manager = order_manager
        self.price_fetcher = price_fetcher
        self.pending_entries: Dict[str, PendingEntry] = {}  # symbol -> entry
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

        # Statistics
        self.stats = {
            "total_entries": 0,
            "limit_fills": 0,
            "chased_entries": 0,
            "pullback_entries": 0,
            "total_improvement_bps": 0.0,
            "avg_wait_seconds": 0.0,
        }

    async def start(self):
        """Start the entry monitor loop."""
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("EntryOptimizer started")

    async def stop(self):
        """Stop the entry monitor."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("EntryOptimizer stopped")

    def get_entry_strategy(self, strategy_name: str, signal_strength: float = 0.5) -> EntryStrategy:
        """
        Determine the best entry strategy based on signal source.

        Args:
            strategy_name: Name of the strategy generating the signal
            signal_strength: Confidence/strength of the signal (0-1)

        Returns:
            EntryStrategy to use
        """
        # High confidence signals should enter immediately
        if signal_strength > 0.85:
            return EntryStrategy.IMMEDIATE

        # Sentiment signals get pullback treatment
        if strategy_name == "sentiment_driven":
            return EntryStrategy.PULLBACK_WAIT

        # Other signals use limit-wait
        return EntryStrategy.LIMIT_WAIT

    def calculate_limit_price(
        self,
        current_price: float,
        side: str,
        offset_pct: float = None
    ) -> float:
        """
        Calculate limit order price for better entry.

        For LONG: price below current (buy the dip)
        For SHORT: price above current (sell the rip)
        """
        offset = offset_pct or self.DEFAULT_LIMIT_OFFSET_PCT

        if side.lower() == "buy":
            # Buy lower
            return current_price * (1 - offset / 100)
        else:
            # Sell higher
            return current_price * (1 + offset / 100)

    def calculate_pullback_target(
        self,
        signal_price: float,
        peak_price: float,
        side: str,
        retracement_pct: float = None
    ) -> float:
        """
        Calculate the pullback target price.

        For LONG: Wait for price to retrace 30% from peak
        For SHORT: Wait for price to retrace 30% from trough
        """
        retrace = retracement_pct or self.PULLBACK_RETRACEMENT_PCT
        move = abs(peak_price - signal_price)

        if side.lower() == "buy":
            # Price went up, wait for pullback down
            return peak_price - (move * retrace)
        else:
            # Price went down, wait for pullback up
            return peak_price + (move * retrace)

    async def submit_entry(
        self,
        symbol: str,
        side: str,
        size: float,
        current_price: float,
        strategy_name: str,
        signal_strength: float = 0.5,
        force_strategy: Optional[EntryStrategy] = None,
        is_closing: bool = False
    ) -> EntryResult:
        """
        Submit an entry with timing optimization.

        For immediate entries, places order right away.
        For optimized entries, queues for monitoring.

        Args:
            symbol: Trading symbol
            side: 'buy' or 'sell'
            size: Position size
            current_price: Current market price
            strategy_name: Strategy generating the signal
            signal_strength: Signal confidence (0-1)
            force_strategy: Override entry strategy selection

        Returns:
            EntryResult with order details
        """
        entry_strategy = force_strategy or self.get_entry_strategy(strategy_name, signal_strength)

        # Closing trades always execute immediately - never delay a risk-reducing trade
        if is_closing:
            entry_strategy = EntryStrategy.IMMEDIATE

        # Immediate entry - just place the order
        if entry_strategy == EntryStrategy.IMMEDIATE:
            return await self._execute_immediate(symbol, side, size, current_price, strategy_name, is_closing=is_closing)

        # Cancel any existing pending entry for this symbol
        if symbol in self.pending_entries:
            await self._cancel_pending(symbol)

        # Calculate limit price
        limit_price = self.calculate_limit_price(current_price, side)

        # Create pending entry
        entry = PendingEntry(
            symbol=symbol,
            side=side,
            target_size=size,
            signal_price=current_price,
            limit_price=limit_price,
            strategy_name=strategy_name,
            entry_strategy=entry_strategy,
            peak_price=current_price,
            max_wait_seconds=(
                self.PULLBACK_WAIT_SECONDS
                if entry_strategy == EntryStrategy.PULLBACK_WAIT
                else self.DEFAULT_WAIT_SECONDS
            )
        )

        # Place initial limit order
        if self.order_manager:
            result = await self.order_manager.create_order(
                symbol=symbol,
                side=side,
                sz=size,
                px=limit_price,
                order_type="limit",
                strategy_name=strategy_name,
                is_closing=is_closing
            )

            if result.success:
                entry.order_id = result.order_id
                entry.status = "placed"
                self.pending_entries[symbol] = entry

                logger.info(f"[EntryOptimizer] {symbol}: Placed {entry_strategy.value} "
                           f"{side} limit @ {limit_price:.4f} (signal @ {current_price:.4f})")

                return EntryResult(
                    success=True,
                    order_id=result.order_id,
                    entry_type=entry_strategy.value,
                    improvement_pct=(current_price - limit_price) / current_price * 100 if side == "buy"
                                   else (limit_price - current_price) / current_price * 100
                )
            else:
                return EntryResult(success=False, error=result.error)

        # No order manager - just track the entry
        self.pending_entries[symbol] = entry
        return EntryResult(
            success=True,
            entry_type=entry_strategy.value,
            improvement_pct=0.0
        )

    async def _execute_immediate(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        strategy_name: str,
        is_closing: bool = False
    ) -> EntryResult:
        """Execute an immediate market/limit order."""
        if not self.order_manager:
            return EntryResult(success=False, error="No order manager")

        result = await self.order_manager.create_order(
            symbol=symbol,
            side=side,
            sz=size,
            px=price,
            order_type="limit",  # Still use limit for price protection
            strategy_name=strategy_name,
            is_closing=is_closing
        )

        self.stats["total_entries"] += 1

        return EntryResult(
            success=result.success,
            order_id=result.order_id,
            fill_price=price,
            entry_type="immediate",
            improvement_pct=0.0,
            error=result.error if not result.success else None
        )

    async def _cancel_pending(self, symbol: str):
        """Cancel a pending entry and its order."""
        if symbol not in self.pending_entries:
            return

        entry = self.pending_entries[symbol]
        if entry.order_id and self.order_manager:
            await self.order_manager.cancel_order(symbol, entry.order_id)

        del self.pending_entries[symbol]
        logger.debug(f"[EntryOptimizer] Cancelled pending entry for {symbol}")

    async def _monitor_loop(self):
        """Monitor pending entries and manage them."""
        while self._running:
            try:
                await self._check_pending_entries()
                await asyncio.sleep(self.CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[EntryOptimizer] Monitor error: {e}")
                await asyncio.sleep(1)

    async def _check_pending_entries(self):
        """Check all pending entries and take action if needed."""
        if not self.price_fetcher:
            return

        now = datetime.now()
        entries_to_remove = []

        for symbol, entry in list(self.pending_entries.items()):
            try:
                current_price = await self.price_fetcher(symbol)
                if current_price is None:
                    continue

                elapsed = (now - entry.created_at).total_seconds()

                # Update peak price for pullback tracking
                if entry.entry_strategy == EntryStrategy.PULLBACK_WAIT:
                    if entry.side == "buy":
                        if current_price > (entry.peak_price or 0):
                            entry.peak_price = current_price
                            entry.pullback_target = self.calculate_pullback_target(
                                entry.signal_price, current_price, entry.side
                            )
                    else:
                        if current_price < (entry.peak_price or float('inf')):
                            entry.peak_price = current_price
                            entry.pullback_target = self.calculate_pullback_target(
                                entry.signal_price, current_price, entry.side
                            )

                # Check if we should chase
                should_chase = False
                chase_reason = ""

                # Timeout - chase with market
                if elapsed > entry.max_wait_seconds:
                    should_chase = True
                    chase_reason = f"timeout after {elapsed:.0f}s"

                # Price moved against us beyond threshold
                if entry.side == "buy":
                    move_pct = (current_price - entry.signal_price) / entry.signal_price * 100
                    if move_pct > self.CHASE_THRESHOLD_PCT:
                        should_chase = True
                        chase_reason = f"price up {move_pct:.2f}%"
                else:
                    move_pct = (entry.signal_price - current_price) / entry.signal_price * 100
                    if move_pct > self.CHASE_THRESHOLD_PCT:
                        should_chase = True
                        chase_reason = f"price down {move_pct:.2f}%"

                # For pullback entries, check if pullback target hit
                if entry.entry_strategy == EntryStrategy.PULLBACK_WAIT and entry.pullback_target:
                    if entry.side == "buy" and current_price <= entry.pullback_target:
                        logger.info(f"[EntryOptimizer] {symbol}: Pullback target hit @ {current_price:.4f}")
                        # Let the existing limit order fill, or place new one at current price
                    elif entry.side == "sell" and current_price >= entry.pullback_target:
                        logger.info(f"[EntryOptimizer] {symbol}: Pullback target hit @ {current_price:.4f}")

                if should_chase:
                    await self._chase_entry(entry, current_price, chase_reason)
                    entries_to_remove.append(symbol)

            except Exception as e:
                logger.warning(f"[EntryOptimizer] Error checking {symbol}: {e}")

        # Remove completed entries
        for symbol in entries_to_remove:
            if symbol in self.pending_entries:
                del self.pending_entries[symbol]

    async def _chase_entry(self, entry: PendingEntry, current_price: float, reason: str):
        """Chase an entry with a market order at current price."""
        if not self.order_manager:
            return

        # Cancel existing limit order
        if entry.order_id:
            await self.order_manager.cancel_order(entry.symbol, entry.order_id)

        # Place market order (using limit at current price for safety)
        # Note: chased entries are always new entries (not closing), since closing
        # trades use IMMEDIATE execution and never reach the chase logic
        result = await self.order_manager.create_order(
            symbol=entry.symbol,
            side=entry.side,
            sz=entry.target_size,
            px=current_price,
            order_type="limit",
            strategy_name=entry.strategy_name,
            is_closing=False
        )

        if result.success:
            # Calculate improvement (negative if we chased at worse price)
            if entry.side == "buy":
                improvement = (entry.signal_price - current_price) / entry.signal_price * 100
            else:
                improvement = (current_price - entry.signal_price) / entry.signal_price * 100

            self.stats["total_entries"] += 1
            self.stats["chased_entries"] += 1
            self.stats["total_improvement_bps"] += improvement * 100

            wait_seconds = (datetime.now() - entry.created_at).total_seconds()
            self.stats["avg_wait_seconds"] = (
                (self.stats["avg_wait_seconds"] * (self.stats["total_entries"] - 1) + wait_seconds)
                / self.stats["total_entries"]
            )

            logger.info(f"[EntryOptimizer] {entry.symbol}: Chased @ {current_price:.4f} "
                       f"(signal @ {entry.signal_price:.4f}, {reason}) "
                       f"improvement: {improvement:+.2f}%")

    def get_stats(self) -> Dict[str, Any]:
        """Get entry optimization statistics."""
        total = self.stats["total_entries"]
        return {
            "total_entries": total,
            "limit_fill_rate": self.stats["limit_fills"] / total if total > 0 else 0,
            "chase_rate": self.stats["chased_entries"] / total if total > 0 else 0,
            "avg_improvement_bps": self.stats["total_improvement_bps"] / total if total > 0 else 0,
            "avg_wait_seconds": self.stats["avg_wait_seconds"],
            "pending_count": len(self.pending_entries),
        }

    def get_pending_entries(self) -> List[Dict]:
        """Get list of pending entries for display."""
        result = []
        for symbol, entry in self.pending_entries.items():
            elapsed = (datetime.now() - entry.created_at).total_seconds()
            result.append({
                "symbol": symbol,
                "side": entry.side,
                "size": entry.target_size,
                "signal_price": entry.signal_price,
                "limit_price": entry.limit_price,
                "strategy": entry.entry_strategy.value,
                "elapsed_seconds": elapsed,
                "max_wait": entry.max_wait_seconds,
                "pullback_target": entry.pullback_target,
            })
        return result
