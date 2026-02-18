"""
WebSocket Streamer - Handles real-time market data updates from Hyperliquid.
Supports live WebSockets and synthetic mock streaming.
"""

import logging
import asyncio
import json
import time
import random
from typing import List, Dict, Any, Optional, Callable, Set
from dataclasses import dataclass, field

from hyperliquid.utils import constants
from hyperliquid.websocket_manager import WebsocketManager

from data_service.utils.config_loader import get_config
from data_service.utils.health_check import get_health

logger = logging.getLogger(__name__)

@dataclass
class TradeUpdate:
    symbol: str
    side: str
    px: float
    sz: float
    time: int
    hash: str

class WebsocketStreamer:
    """
    Manages WebSocket subscriptions and real-time data flow.
    Supports callbacks for tickers, L2 books, and trades.
    """
    def __init__(self, mode: Optional[str] = None):
        self.config = get_config()
        self.health = get_health()
        self.mode = mode or ("mock" if self.config.is_mock_mode() else "live")
        
        self.ticker_callbacks: Dict[str, Set[Callable]] = {}
        self.book_callbacks: Dict[str, Set[Callable]] = {}
        self.trade_callbacks: Dict[str, Set[Callable]] = {}
        
        self.active_symbols: Set[str] = set()
        self.running = False
        self._mock_task: Optional[asyncio.Task] = None
        
        if self.mode == "live":
            base_url = self.config.hyperliquid.get("api_base_url", constants.TESTNET_API_URL)
            self.ws_manager = WebsocketManager(base_url)
            logger.info(f"WebsocketStreamer initialized in LIVE mode ({base_url})")
        else:
            logger.info("WebsocketStreamer initialized in MOCK mode")

    def subscribe_ticker(self, symbol: str, callback: Callable):
        """Subscribe to 1-Hz ticker updates (mids)."""
        if symbol not in self.ticker_callbacks:
            self.ticker_callbacks[symbol] = set()
        self.ticker_callbacks[symbol].add(callback)
        self.active_symbols.add(symbol)
        
        if self.mode == "live" and self.running:
            self.ws_manager.subscribe({"type": "allMids"}, self._on_all_mids)

    def subscribe_book(self, symbol: str, callback: Callable):
        """Subscribe to Level-2 book updates."""
        if symbol not in self.book_callbacks:
            self.book_callbacks[symbol] = set()
        self.book_callbacks[symbol].add(callback)
        self.active_symbols.add(symbol)
        
        if self.mode == "live" and self.running:
            self.ws_manager.subscribe({"type": "l2Book", "coin": symbol}, self._on_book_update)

    def subscribe_trades(self, symbol: str, callback: Callable):
        """Subscribe to individual trade events."""
        if symbol not in self.trade_callbacks:
            self.trade_callbacks[symbol] = set()
        self.trade_callbacks[symbol].add(callback)
        self.active_symbols.add(symbol)
        
        if self.mode == "live" and self.running:
            self.ws_manager.subscribe({"type": "trades", "coin": symbol}, self._on_trades_update)

    async def start(self):
        """Start the streaming service."""
        if self.running:
            return
        
        self.running = True
        if self.mode == "mock":
            self._mock_task = asyncio.create_task(self._run_mock_stream())
        else:
            # Re-subscribe all if it's a restart
            # Note: HL WebsocketManager handle async loop internally
            for symbol in self.active_symbols:
                if symbol in self.ticker_callbacks:
                    self.ws_manager.subscribe({"type": "allMids"}, self._on_all_mids)
                if symbol in self.book_callbacks:
                    self.ws_manager.subscribe({"type": "l2Book", "coin": symbol}, self._on_book_update)
                if symbol in self.trade_callbacks:
                    self.ws_manager.subscribe({"type": "trades", "coin": symbol}, self._on_trades_update)
        
        self.health.record_ws_connection(True)
        logger.info("WebsocketStreamer started.")

    async def stop(self):
        """Stop the streaming service."""
        self.running = False
        if self._mock_task:
            self._mock_task.cancel()
            try:
                await self._mock_task
            except asyncio.CancelledError:
                pass
        
        self.health.record_ws_connection(False)
        logger.info("WebsocketStreamer stopped.")

    # --- Live Handlers ---

    def _on_all_mids(self, data: Any):
        """Handle 1-Hz ticker broadcast."""
        if not data or 'data' not in data or 'mids' not in data['data']:
            return
            
        params = data['data']['mids']
        for symbol, mid in params.items():
            if symbol in self.ticker_callbacks:
                for cb in self.ticker_callbacks[symbol]:
                    cb(symbol, float(mid))

    def _on_book_update(self, data: Any):
        """Handle L2 book snapshot or diff."""
        if not data or 'data' not in data:
            return
            
        symbol = data['data']['coin']
        if symbol in self.book_callbacks:
            for cb in self.book_callbacks[symbol]:
                cb(symbol, data['data'])

    def _on_trades_update(self, data: Any):
        """Handle individual trade updates."""
        if not data or 'data' not in data:
            return
            
        symbol = data['data'][0]['coin'] if isinstance(data['data'], list) and data['data'] else None
        if symbol and symbol in self.trade_callbacks:
            for cb in self.trade_callbacks[symbol]:
                cb(symbol, data['data'])

    # --- Mock Streaming ---

    async def _run_mock_stream(self):
        """Emission of synthetic data at 1-Hz."""
        mock_prices = {s: 2000.0 for s in self.active_symbols}
        
        while self.running:
            try:
                for symbol in list(self.active_symbols):
                    # 1. Ticker updates
                    if symbol in self.ticker_callbacks:
                        # Random walk
                        mock_prices[symbol] *= (1 + random.normalvariate(0, 0.0005))
                        for cb in self.ticker_callbacks[symbol]:
                            cb(symbol, mock_prices[symbol])
                    
                    # 2. Periodic trade updates (10% chance per second)
                    if symbol in self.trade_callbacks and random.random() < 0.1:
                        trade = {
                            "coin": symbol,
                            "side": "B" if random.random() > 0.5 else "S",
                            "px": mock_prices[symbol],
                            "sz": random.uniform(0.1, 5.0),
                            "time": int(time.time() * 1000),
                            "hash": f"mock_hash_{random.getrandbits(64)}"
                        }
                        for cb in self.trade_callbacks[symbol]:
                            cb(symbol, [trade])
                            
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error in mock stream: {e}")
                await asyncio.sleep(1)
