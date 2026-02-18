"""
Hyperliquid Order Executor - Responsible for order placement, cancellation, and position tracking.
Supports live execution via Hyperliquid SDK and in-memory mock execution.
"""

import logging
import asyncio
import time
import random
import json
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass, field, asdict

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
import eth_account

from data_service.utils.config_loader import get_config
from data_service.utils.health_check import get_health
from data_service.utils.hip3_mapping import to_hip3_symbol, from_hip3_symbol, is_hip3_asset
from data_service.utils.rate_limiter import get_rate_limiter_sync

logger = logging.getLogger(__name__)

@dataclass
class OrderResponse:
    success: bool
    order_id: Optional[int] = None
    status: str = "failed"
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

@dataclass
class OpenOrder:
    symbol: str
    side: str
    price: float
    size: float
    order_id: int
    timestamp: float

@dataclass
class Position:
    symbol: str
    size: float  # Positive for long, negative for short
    entry_price: float
    liquidation_price: Optional[float] = None
    unrealized_pnl: float = 0.0
    leverage: float = 1.0

@dataclass
class UserState:
    equity: float
    available_margin: float
    initial_margin_used: float
    maintenance_margin_used: float
    leverage: float
    timestamp: float = field(default_factory=time.time)

@dataclass
class TradeRecord:
    """Record of an executed trade for history tracking."""
    trade_id: int
    order_id: int
    symbol: str
    side: str
    size: float
    price: float
    fill_price: float
    slippage: float
    fee: float
    pnl: float
    timestamp: float
    strategy: str = ""


class MockLedger:
    """
    Realistic mock trading ledger with:
    - Configurable fees (maker/taker)
    - Slippage simulation based on order size
    - Complete trade history
    - Real-time PnL tracking
    - Notification callbacks
    """
    def __init__(self, initial_equity: float = 100000.0):
        self.open_orders: List[OpenOrder] = []
        self.positions: Dict[str, Position] = {}
        self.equity = initial_equity
        self.initial_equity = initial_equity
        self.order_id_counter = 1000
        self.trade_id_counter = 1

        # Trade history
        self.trade_history: List[TradeRecord] = []

        # Fee structure (realistic for Hyperliquid)
        self.maker_fee = 0.0002   # 0.02% maker
        self.taker_fee = 0.0005   # 0.05% taker

        # Slippage model parameters
        self.base_slippage_bps = 1  # 1 bps base slippage
        self.size_impact_factor = 0.001  # Additional slippage per $1000 notional

        # Notification callback
        self._trade_callbacks: List[callable] = []

        # Price reference (set externally)
        self._price_ref: Dict[str, float] = {}

        logger.info(f"MockLedger initialized with ${initial_equity:,.2f} equity")

    def set_price(self, symbol: str, price: float):
        """Set reference price for slippage calculation."""
        self._price_ref[symbol] = price

    def register_trade_callback(self, callback: callable):
        """Register callback for trade notifications."""
        self._trade_callbacks.append(callback)

    def _notify_trade(self, trade: TradeRecord):
        """Notify all registered callbacks and persist to JSONL log."""
        # Notify callbacks
        for cb in self._trade_callbacks:
            try:
                cb(trade)
            except Exception as e:
                logger.error(f"Trade callback error: {e}")

        # Persist to JSON Lines file
        try:
            trade_log_path = Path("logs/trades.jsonl")
            trade_log_path.parent.mkdir(exist_ok=True, parents=True)

            with open(trade_log_path, "a") as f:
                trade_dict = asdict(trade)
                f.write(json.dumps(trade_dict) + "\n")
        except Exception as e:
            logger.error(f"Trade logging error: {e}")

    def _calculate_slippage(self, symbol: str, side: str, sz: float, px: float) -> float:
        """Calculate realistic slippage based on order size."""
        notional = sz * px
        # Base slippage + size impact
        slippage_bps = self.base_slippage_bps + (notional / 1000) * self.size_impact_factor
        slippage_pct = slippage_bps / 10000

        # Slippage works against us
        if side.lower() == "buy":
            return px * (1 + slippage_pct)
        else:
            return px * (1 - slippage_pct)

    def _calculate_fee(self, notional: float, is_taker: bool = True) -> float:
        """Calculate trading fee."""
        fee_rate = self.taker_fee if is_taker else self.maker_fee
        return notional * fee_rate

    def place_order(self, symbol: str, side: str, sz: float, px: Optional[float],
                    strategy: str = "") -> OrderResponse:
        oid = self.order_id_counter
        self.order_id_counter += 1

        # Get reference price
        ref_price = self._price_ref.get(symbol, px or 100.0)

        # Market order - fill immediately with slippage
        if px is None:
            fill_px = self._calculate_slippage(symbol, side, sz, ref_price)
            notional = sz * fill_px
            fee = self._calculate_fee(notional, is_taker=True)

            # Calculate PnL for closing trades
            pnl = 0.0
            if symbol in self.positions:
                pos = self.positions[symbol]
                # Check if reducing position
                is_reducing = (pos.size > 0 and side.lower() == "sell") or \
                              (pos.size < 0 and side.lower() == "buy")
                if is_reducing:
                    close_sz = min(abs(pos.size), sz)
                    if pos.size > 0:
                        pnl = close_sz * (fill_px - pos.entry_price)
                    else:
                        pnl = close_sz * (pos.entry_price - fill_px)
                    pnl -= fee  # Deduct fee from PnL

            self._update_position(symbol, side, sz, fill_px)
            self.equity -= fee  # Deduct fee from equity
            self.equity += pnl  # Add/subtract realized PnL

            # Record trade
            trade = TradeRecord(
                trade_id=self.trade_id_counter,
                order_id=oid,
                symbol=symbol,
                side=side,
                size=sz,
                price=ref_price,
                fill_price=fill_px,
                slippage=(fill_px - ref_price) / ref_price if ref_price else 0,
                fee=fee,
                pnl=pnl,
                timestamp=time.time(),
                strategy=strategy
            )
            self.trade_history.append(trade)
            self.trade_id_counter += 1
            self._notify_trade(trade)

            logger.info(f"MOCK FILL: {side.upper()} {sz:.4f} {symbol} @ {fill_px:.2f} "
                       f"(slip={trade.slippage*10000:.1f}bps, fee=${fee:.2f}, pnl=${pnl:.2f})")

            return OrderResponse(success=True, order_id=oid, status="filled")

        # Limit order - add to book
        order = OpenOrder(symbol, side, px, sz, oid, time.time())
        self.open_orders.append(order)
        return OrderResponse(success=True, order_id=oid, status="open")

    def _update_position(self, symbol: str, side: str, sz: float, px: float):
        signed_sz = sz if side.lower() == "buy" else -sz
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol, signed_sz, px)
        else:
            pos = self.positions[symbol]
            new_sz = pos.size + signed_sz
            if abs(new_sz) < 1e-8:
                del self.positions[symbol]
                return

            # Weighted average entry price for adding, keep entry for reducing
            if (pos.size > 0 and signed_sz > 0) or (pos.size < 0 and signed_sz < 0):
                pos.entry_price = (pos.size * pos.entry_price + signed_sz * px) / new_sz

            pos.size = new_sz

    def cancel_order(self, oid: int) -> bool:
        original_len = len(self.open_orders)
        self.open_orders = [o for o in self.open_orders if o.order_id != oid]
        return len(self.open_orders) < original_len

    def get_trade_stats(self) -> Dict[str, Any]:
        """Get trading statistics."""
        if not self.trade_history:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "total_fees": 0.0,
                "avg_slippage_bps": 0.0
            }

        trades_with_pnl = [t for t in self.trade_history if t.pnl != 0]
        wins = len([t for t in trades_with_pnl if t.pnl > 0])
        losses = len([t for t in trades_with_pnl if t.pnl < 0])

        return {
            "total_trades": len(self.trade_history),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(trades_with_pnl) if trades_with_pnl else 0.0,
            "total_pnl": sum(t.pnl for t in self.trade_history),
            "total_fees": sum(t.fee for t in self.trade_history),
            "avg_slippage_bps": np.mean([abs(t.slippage) * 10000 for t in self.trade_history]),
            "return_pct": (self.equity - self.initial_equity) / self.initial_equity * 100
        }

    def get_recent_trades(self, limit: int = 50) -> List[Dict]:
        """Get recent trades as dictionaries."""
        return [
            {
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "side": t.side,
                "size": t.size,
                "price": t.fill_price,
                "fee": t.fee,
                "pnl": t.pnl,
                "timestamp": t.timestamp,
                "strategy": t.strategy
            }
            for t in self.trade_history[-limit:]
        ]

class HyperliquidExecutor:
    """
    Handles trading operations on Hyperliquid.
    Manages retry logic, live API integration, and mock simulation.
    """
    def __init__(self, mode: Optional[str] = None):
        self.config = get_config()
        self.health = get_health()
        self.mode = mode or ("mock" if self.config.is_mock_mode() else "live")
        
        if self.mode == "live":
            self.address = self.config.hyperliquid.get("wallet_address")
            self.secret = self.config.hyperliquid.get("secret_key")
            
            if not self.address or not self.secret or self.address.startswith("${"):
                logger.warning("Live credentials missing or placeholders found. Falling back to MOCK mode.")
                self.mode = "mock"
                self.mock_ledger = MockLedger()
            else:
                self.account = eth_account.Account.from_key(self.secret)
                base_url = self.config.hyperliquid.get("api_base_url", constants.TESTNET_API_URL)

                # Initialize Exchange with HIP-3 DEX support
                # Pass perp_dexs to enable trading on builder-deployed perp DEXes
                hip3_dexs = ["xyz", "flx"]  # XYZ and Felix Exchange DEXes
                self.exchange = Exchange(self.account, base_url, perp_dexs=hip3_dexs)
                self.info = Info(base_url, skip_ws=True, perp_dexs=hip3_dexs)

                logger.info(f"Initialized with HIP-3 DEXes: {hip3_dexs}")

                # NOTE: Removed _load_hip3_metas() - SDK handles HIP-3 asset mapping
                # automatically when perp_dexs is passed to constructor. Manual loading
                # was overwriting correct 110xxx IDs with wrong sequential offsets.

                # Cache szDecimals for proper order rounding
                self._cache_sz_decimals()

                logger.info(f"HyperliquidExecutor initialized in LIVE mode for {self.address}")
        else:
            self.mock_ledger = MockLedger()
            logger.info("HyperliquidExecutor initialized in MOCK mode")

    def _get_sz_decimals(self, symbol: str) -> int:
        """Get size decimals for an asset (for proper rounding)."""
        # Use cached value if available
        if hasattr(self, '_sz_decimals_cache') and symbol in self._sz_decimals_cache:
            return self._sz_decimals_cache[symbol]
        return 3  # Default to 3 decimals

    def _cache_sz_decimals(self):
        """Cache szDecimals for all assets to avoid repeated API calls."""
        self._sz_decimals_cache = {}
        try:
            all_metas = self.exchange.info.post('/info', {'type': 'allPerpMetas'})
            for dex in all_metas:
                for asset in dex.get('universe', []):
                    name = asset.get('name')
                    sz_dec = asset.get('szDecimals', 3)
                    self._sz_decimals_cache[name] = sz_dec
            logger.debug(f"Cached szDecimals for {len(self._sz_decimals_cache)} assets")
        except Exception as e:
            logger.warning(f"Failed to cache szDecimals: {e}")

    def _load_hip3_metas(self):
        """Load HIP-3 DEX metas into SDK so it can resolve HIP-3 asset names."""
        try:
            # Must load into EXCHANGE's info, not our own info (exchange uses its own)
            all_metas = self.exchange.info.post('/info', {'type': 'allPerpMetas'})

            # Calculate offset for each DEX and load HIP-3 metas
            offset = 0
            loaded_count = 0
            for dex_idx, dex_meta in enumerate(all_metas):
                universe_size = len(dex_meta.get('universe', []))
                if dex_idx > 0:  # Skip DEX 0 (main perps, already loaded)
                    self.exchange.info.set_perp_meta(dex_meta, offset)
                    loaded_count += universe_size
                offset += universe_size

            logger.info(f"Loaded {loaded_count} HIP-3 assets from {len(all_metas)-1} DEXes")
        except Exception as e:
            logger.warning(f"Failed to load HIP-3 metas: {e}")

    async def _retry_call(self, func, *args, timeout_override: Optional[float] = None, **kwargs):
        """
        Execute a function with retry logic and timeout enforcement.

        Args:
            func: The function to call
            *args: Positional arguments for func
            timeout_override: Override default timeout (seconds)
            **kwargs: Keyword arguments for func

        Returns:
            Function result

        Raises:
            Exception on final failure after all retries
        """
        max_retries = 3
        # Get timeout from config or use override (default 30s)
        config = get_config()
        timeout = timeout_override or config.hyperliquid.get('request_timeout_seconds', 30)

        # Get rate limiter (uses config rate limit)
        rate_limit = config.hyperliquid.get('rate_limit_requests_per_second', 5)
        rate_limiter = get_rate_limiter_sync(rate=rate_limit, capacity=rate_limit * 2)

        for i in range(max_retries):
            try:
                # Wait for rate limit token
                await rate_limiter.wait()

                self.health.record_api_call()

                # Run the function with timeout
                if asyncio.iscoroutinefunction(func):
                    return await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=timeout
                    )
                else:
                    # Sync function - run in executor with timeout
                    loop = asyncio.get_event_loop()
                    return await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: func(*args, **kwargs)),
                        timeout=timeout
                    )

            except asyncio.TimeoutError:
                self.health.record_error()
                logger.error(f"API call timed out after {timeout}s: {func.__name__}")
                if i == max_retries - 1:
                    raise TimeoutError(f"API call {func.__name__} timed out after {max_retries} attempts")
                delay = 1.0 * (2 ** i) + random.uniform(0, 0.1)
                logger.warning(f"Timeout retry {i+1}/{max_retries} after {delay:.2f}s")
                await asyncio.sleep(delay)

            except Exception as e:
                # Check for fail-fast errors (e.g., Margin, Invalid Params)
                err_msg = str(e).lower()
                if "margin" in err_msg or "insufficient" in err_msg or "invalid" in err_msg:
                    logger.error(f"Fail-fast error: {e}")
                    raise

                if i == max_retries - 1:
                    self.health.record_error()
                    logger.error(f"Exchange call failed after {max_retries} attempts: {e}")
                    raise

                delay = 1.0 * (2 ** i) + random.uniform(0, 0.1)
                logger.warning(f"Exchange retry {i+1} after {delay:.2f}s: {e}")
                await asyncio.sleep(delay)

    def set_price(self, symbol: str, price: float):
        """Set reference price for mock execution slippage calculation."""
        if self.mode == "mock":
            self.mock_ledger.set_price(symbol, price)

    def get_trade_stats(self) -> Dict[str, Any]:
        """Get mock trading statistics."""
        if self.mode == "mock":
            return self.mock_ledger.get_trade_stats()
        return {}

    def get_recent_trades(self, limit: int = 50) -> List[Dict]:
        """Get recent trades from mock ledger."""
        if self.mode == "mock":
            return self.mock_ledger.get_recent_trades(limit)
        return []

    def register_trade_callback(self, callback: callable):
        """Register callback for trade notifications (mock mode only)."""
        if self.mode == "mock":
            self.mock_ledger.register_trade_callback(callback)

    async def place_order(self, symbol: str, side: str, sz: float, px: Optional[float] = None,
                          order_type: str = "limit", leverage: float = 1.0,
                          strategy: str = "") -> OrderResponse:
        """Place an order (Market or Limit) with validation."""
        side = side.lower()
        if side not in ["buy", "sell"]:
            return OrderResponse(success=False, error=f"Invalid side: {side}. Must be 'buy' or 'sell'.")

        # 3.2.3 Validation against asset config
        asset_config = self.config.get_asset(symbol)
        if not asset_config:
            return OrderResponse(success=False, error=f"Asset {symbol} not found in configuration.")

        min_sz = asset_config.min_order_size
        if sz < min_sz:
            return OrderResponse(success=False, error=f"Order size ({sz}) less than minimum ({min_sz}) for {symbol}.")

        max_lev = asset_config.max_leverage
        if leverage > max_lev:
            return OrderResponse(success=False, error=f"Requested leverage ({leverage}) exceeds maximum ({max_lev}) for {symbol}.")

        is_buy = side == "buy"

        if self.mode == "mock":
            res = self.mock_ledger.place_order(symbol, side, sz, px, strategy=strategy)
            if res.success:
                self.health.record_order()
            return res

        # Live Implementation
        try:
            # Convert to HIP-3 symbol if needed (e.g., XAU -> flx:GOLD)
            hip3_symbol = to_hip3_symbol(symbol)
            logger.debug(f"Symbol mapping: {symbol} -> {hip3_symbol}")

            # Convert market to limit with slippage if px is None
            if px is None:
                # Need mid price for slippage
                # For HIP-3 assets, query the correct DEX (e.g., 'xyz' for stocks, 'flx' for metals)
                # all_mids() with no dex only returns main perp prices
                if is_hip3_asset(symbol):
                    dex = hip3_symbol.split(':')[0] if ':' in hip3_symbol else ''
                    mids = self.info.all_mids(dex=dex)
                else:
                    mids = self.info.all_mids()
                # Try both internal and HIP-3 symbol
                mid = float(mids.get(hip3_symbol, 0)) or float(mids.get(symbol, 0))
                
                # FALLBACK: If mid still 0, try fetching L2 book directly for HIP-3
                if mid == 0 and is_hip3_asset(symbol):
                    try:
                        l2 = await self._retry_call(self.info.post, '/info', {'type': 'l2Book', 'coin': hip3_symbol})
                        bids = l2.get('levels', [[]])[0]
                        asks = l2.get('levels', [[], []])[1]
                        if bids and asks:
                            mid = (float(bids[0]['px']) + float(asks[0]['px'])) / 2
                    except Exception as e:
                        logger.warning(f"Fallback price fetch failed for {hip3_symbol}: {e}")

                if mid == 0:
                    return OrderResponse(success=False, error=f"Could not fetch price for {hip3_symbol}")
                # 5% slippage protection
                px = mid * 1.05 if is_buy else mid * 0.95
                order_type = "market"

            # Get size decimals for proper rounding (avoid float_to_wire error)
            sz_decimals = self._get_sz_decimals(hip3_symbol)
            sz = round(sz, sz_decimals)

            # Round price to reasonable precision (typically 5-6 significant figures)
            if px > 1000:
                px = round(px, 1)
            elif px > 100:
                px = round(px, 2)
            elif px > 1:
                px = round(px, 4)
            else:
                px = round(px, 6)

            logger.debug(f"Order: {hip3_symbol} {side} sz={sz} (decimals={sz_decimals}) px={px}")

            # Execute order with HIP-3 symbol
            # SDK signature: order(name, is_buy, sz, limit_px, order_type, reduce_only=False, ...)
            ot = {"limit": {"tif": "Gtc"}} if order_type == "limit" else {"limit": {"tif": "Ioc"}}
            res = await self._retry_call(
                self.exchange.order,
                hip3_symbol,  # Use HIP-3 symbol (e.g., flx:GOLD)
                is_buy,
                sz,
                px,
                ot
            )

            # Log the API response for debugging
            logger.info(f"Order response for {hip3_symbol}: {res}")

            if res["status"] == "ok":
                status_data = res["response"]["data"]["statuses"][0]
                if "resting" in status_data:
                    self.health.record_order()
                    oid = status_data["resting"]["oid"]
                    logger.info(f"ORDER RESTING: {side.upper()} {sz} {hip3_symbol} @ {px} (oid={oid})")
                    return OrderResponse(success=True, order_id=oid, status="open")
                elif "filled" in status_data:
                    self.health.record_order()
                    oid = status_data["filled"]["oid"]
                    fill_px = status_data["filled"].get("avgPx", px)
                    logger.info(f"ORDER FILLED: {side.upper()} {sz} {hip3_symbol} @ {fill_px} (oid={oid})")
                    return OrderResponse(success=True, order_id=oid, status="filled")
                else:
                    logger.warning(f"Order status unexpected: {status_data}")
                    return OrderResponse(success=False, error=str(status_data))
            else:
                return OrderResponse(success=False, error=res.get("response", "Unknown error"))
                
        except Exception as e:
            return OrderResponse(success=False, error=str(e))

    async def cancel_order(self, symbol: str, oid: int) -> bool:
        """Cancel an open order."""
        if self.mode == "mock":
            return self.mock_ledger.cancel_order(oid)

        # Live Implementation - SDK requires HIP-3 symbol (e.g., 'xyz:TSLA' not 'TSLA')
        hip3_symbol = to_hip3_symbol(symbol)
        res = await self._retry_call(self.exchange.cancel, hip3_symbol, oid)
        return res["status"] == "ok"

    async def get_positions(self) -> List[Position]:
        """Fetch all currently open positions including HIP-3 DEXes."""
        if self.mode == "mock":
            return list(self.mock_ledger.positions.values())

        # Live Implementation - query all clearinghouses (main + HIP-3 DEXes)
        positions = []
        dexes = [None, 'xyz', 'flx']  # None = main perps, then HIP-3 DEXes

        for dex in dexes:
            try:
                # Use direct API call for DEX-specific clearinghouse
                payload = {'type': 'clearinghouseState', 'user': self.address}
                if dex:
                    payload['dex'] = dex

                state = await self._retry_call(self.info.post, '/info', payload)

                for p in state.get('assetPositions', []):
                    pos_data = p['position']
                    size = float(pos_data['szi'])
                    if abs(size) > 1e-8:
                        positions.append(Position(
                            symbol=pos_data['coin'],
                            size=size,
                            entry_price=float(pos_data['entryPx']),
                            liquidation_price=float(pos_data['liquidationPx']) if pos_data.get('liquidationPx') else None,
                            unrealized_pnl=float(pos_data.get('unrealizedPnl', 0)),
                            leverage=float(pos_data['leverage']['value']) if isinstance(pos_data.get('leverage'), dict) else 1.0
                        ))
            except Exception as e:
                logger.warning(f"Error fetching positions from {dex or 'main'} DEX: {e}")

        return positions

    async def get_open_orders(self) -> List[OpenOrder]:
        """Fetch all currently open limit orders."""
        if self.mode == "mock":
            return self.mock_ledger.open_orders
        
        # Live Implementation
        orders = await self._retry_call(self.info.open_orders, self.address)
        return [
            OpenOrder(
                symbol=o['coin'],
                side="buy" if float(o['sz']) > 0 else "sell",
                price=float(o['limitPx']),
                size=abs(float(o['sz'])),
                order_id=o['oid'],
                timestamp=o['timestamp'] / 1000.0
            ) for o in orders
        ]

    async def get_user_state(self) -> UserState:
        """Fetch account margin and equity summary."""
        if self.mode == "mock":
            return UserState(
                equity=self.mock_ledger.equity,
                available_margin=self.mock_ledger.equity,
                initial_margin_used=0.0,
                maintenance_margin_used=0.0,
                leverage=1.0
            )
        
        # Live Implementation
        state = await self._retry_call(self.info.user_state, self.address)
        return UserState(
            equity=float(state['marginSummary']['accountValue']),
            available_margin=float(state['withdrawable']),
            initial_margin_used=float(state['marginSummary']['totalInitialMargin']),
            maintenance_margin_used=float(state['marginSummary']['totalMaintenanceMargin']),
            leverage=1.0 # Need to iterate through positions to get effective leverage if needed
        )

    async def get_user_fills(self) -> List[Dict[str, Any]]:
        """Fetch recent execution history (fills) for the account."""
        if self.mode == "mock":
            # Map mock trade history to fill format
            return [
                {
                    "coin": t.symbol,
                    "px": str(t.fill_price),
                    "sz": str(t.size),
                    "side": "B" if t.side.lower() == "buy" else "S",
                    "time": int(t.timestamp * 1000),
                    "hash": f"mock_{t.trade_id}",
                    "oid": t.order_id,
                    "fee": str(t.fee),
                    "realizedPnl": str(t.pnl)
                }
                for t in self.mock_ledger.trade_history
            ]

        # Live Implementation: Query all DEXes for fills
        all_fills = []
        # Main perp + HIP-3 DEXes
        dexes = [None, 'xyz', 'flx']
        
        for dex in dexes:
            try:
                payload = {'type': 'userFills', 'user': self.address}
                if dex:
                    payload['dex'] = dex
                
                fills = await self._retry_call(self.info.post, '/info', payload)
                if isinstance(fills, list):
                    all_fills.extend(fills)
            except Exception as e:
                logger.error(f"Error fetching fills for {dex or 'main'} DEX: {e}")

        # Sort by time descending
        all_fills.sort(key=lambda x: x.get('time', 0), reverse=True)
        return all_fills
