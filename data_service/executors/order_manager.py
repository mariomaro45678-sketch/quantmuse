"""
Order Manager - Manages order lifecycle, tracking, and persistence.
Phase 7: Integrated with RiskManager for pre-trade risk validation.
"""

import logging
import time
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime

from data_service.executors.hyperliquid_executor import HyperliquidExecutor, OrderResponse
from data_service.storage.order_storage import OrderStorage

logger = logging.getLogger(__name__)

class OrderManager:
    """
    Wraps the HyperliquidExecutor to provide higher-level order management.
    Handles tracking of active orders, P&L calculation, and trade persistence.
    
    Phase 7: Integrated with RiskManager for pre-trade risk checks.
    """
    def __init__(self, executor: Optional[HyperliquidExecutor] = None, 
                 storage: Optional[OrderStorage] = None,
                 risk_manager=None):  # Optional RiskManager
        self.executor = executor or HyperliquidExecutor()
        self.storage = storage or OrderStorage()
        self.risk_manager = risk_manager  # Will be None if not provided
        self.active_orders: Dict[int, Dict[str, Any]] = {}
        logger.info("OrderManager initialized.")

    async def create_order(self, symbol: str, side: str, sz: float, px: Optional[float] = None,
                           order_type: str = "limit", strategy_name: str = "unknown",
                           leverage: float = 1.0, is_closing: bool = False) -> OrderResponse:
        """
        Create and track a new order.

        Phase 7: Includes pre-trade risk check before execution.
        """
        # Phase 7: Pre-trade risk check
        if self.risk_manager and px:
            check = self.risk_manager.pre_trade_check(
                symbol=symbol, side=side, size=sz, leverage=leverage,
                price=px, is_closing=is_closing, strategy_name=strategy_name
            )
            if not check.approved:
                logger.warning(f"❌ Order REJECTED by risk check | {symbol} {side} {sz} @ {px} | {check.reason}")
                # Return rejected order response (OrderResponse only has: success, status, order_id, error)
                return OrderResponse(
                    success=False,
                    status='rejected',
                    order_id=None,
                    error=f'Risk check failed: {check.reason}'
                )

        # Set reference price for mock execution (for realistic slippage)
        if px:
            self.executor.set_price(symbol, px)

        res = await self.executor.place_order(symbol, side, sz, px, order_type,
                                              leverage=leverage, strategy=strategy_name)
        
        if res.success and res.order_id:
            order_record = {
                "order_id": res.order_id,
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "size": sz,
                "price": px,
                "status": res.status,
                "strategy_name": strategy_name,
                "created_at": datetime.now().isoformat(),
                "fill_price": None,
                "realized_pnl": 0.0
            }
            
            if res.status != "filled":
                self.active_orders[res.order_id] = order_record
            
            # Persist initial record
            self.storage.save_order(order_record)
            logger.info(f"Order {res.order_id} created for {symbol} ({strategy_name})")
            
        return res

    async def cancel_order(self, symbol: str, order_id: int) -> bool:
        """Cancel an active order."""
        success = await self.executor.cancel_order(symbol, order_id)
        if success:
            if order_id in self.active_orders:
                order = self.active_orders.pop(order_id)
                order['status'] = 'cancelled'
                order['closed_at'] = datetime.now().isoformat()
                self.storage.save_order(order)
                logger.info(f"Order {order_id} cancelled.")
        return success

    async def sync_orders(self):
        """
        Synchronize active orders and reconciles fills with the exchange.
        
        This handles both confirming open orders and calculating P&L from fills.
        """
        try:
            # 1. Reconciliation of fills (updates P&L and fill prices)
            await self.reconcile_fills()

            if not self.active_orders:
                return

            # 2. Fetch actual open orders from exchange to detect orphaned ones
            exchange_orders = await self.executor.get_open_orders()
            exchange_order_ids = {o.order_id for o in exchange_orders}

            # Find orders that are in our tracking but not on exchange
            # These are either filled, cancelled, or expired
            orphaned_orders = []
            for order_id, order in list(self.active_orders.items()):
                if order_id not in exchange_order_ids:
                    orphaned_orders.append(order_id)

            # Update orphaned orders
            for order_id in orphaned_orders:
                order = self.active_orders.pop(order_id)
                # If it wasn't already marked filled by reconcile_fills,
                # mark as filled_unconfirmed (conservative assumption)
                if order['status'] == 'open':
                    order['status'] = 'filled_unconfirmed'
                    order['closed_at'] = datetime.now().isoformat()
                    self.storage.save_order(order)
                    logger.warning(f"sync_orders: Order {order_id} ({order['symbol']}) "
                                 f"not found on exchange - marked as filled_unconfirmed")

            # Log sync result
            if orphaned_orders:
                logger.info(f"sync_orders: Reconciled {len(orphaned_orders)} completed orders")
            else:
                logger.debug(f"sync_orders: All {len(self.active_orders)} orders in sync")

        except Exception as e:
            logger.error(f"sync_orders failed: {e}")

    async def reconcile_fills(self):
        """
        Fetch execution history and update trade records with actual prices and P&L.
        """
        try:
            fills = await self.executor.get_user_fills()
            if not fills:
                return

            # Get recent history from storage to find trades that need P&L info
            # Only check last 100 trades to keep it efficient
            recent_trades = self.storage.get_history(limit=100)
            
            # Map fills by order_id for quick lookup
            fills_by_oid = {}
            for f in fills:
                oid = f.get('oid')
                if oid:
                    if oid not in fills_by_oid:
                        fills_by_oid[oid] = []
                    fills_by_oid[oid].append(f)

            updated_count = 0
            for trade in recent_trades:
                # We update if P&L is 0/NULL or status is 'open'/'filled_unconfirmed'
                if trade.get('realized_pnl') in [0.0, None] or trade.get('status') in ['open', 'filled_unconfirmed']:
                    oid = trade.get('order_id')
                    if oid in fills_by_oid:
                        trade_fills = fills_by_oid[oid]
                        
                        # Aggregate fill data
                        total_sz = sum(float(f['sz']) for f in trade_fills)
                        avg_px = sum(float(f['px']) * float(f['sz']) for f in trade_fills) / total_sz if total_sz > 0 else 0
                        
                        # Update trade record
                        trade['fill_price'] = avg_px
                        trade['status'] = 'filled'
                        trade['closed_at'] = trade.get('closed_at') or datetime.now().isoformat()
                        
                        # Use closedPnl from the exchange if available
                        # Hyperliquid's userFills API provides 'closedPnl' for trades that close a position.
                        exchange_pnl = sum(float(f.get('closedPnl', 0)) for f in trade_fills)
                        
                        trade['realized_pnl'] = exchange_pnl
                        
                        # Save updated record
                        self.storage.save_order(trade)
                        updated_count += 1
                        
                        # If it was in active_orders, remove it
                        if oid in self.active_orders:
                            self.active_orders.pop(oid)

            if updated_count > 0:
                logger.info(f"reconcile_fills: Updated {updated_count} trades with fill info")

        except Exception as e:
            logger.error(f"reconcile_fills failed: {e}")

    def get_order_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve historical orders from storage."""
        return self.storage.get_history(limit)
