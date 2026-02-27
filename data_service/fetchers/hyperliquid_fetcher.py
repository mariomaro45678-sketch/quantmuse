"""
Hyperliquid Data Fetcher - Responsible for retrieving market data from Hyperliquid.
Supports both live API and deterministic mock mode.
"""

import logging
import asyncio
import time
import random
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass, field

from hyperliquid.info import Info
from hyperliquid.utils import constants

from data_service.utils.config_loader import get_config
from data_service.utils.health_check import get_health
from data_service.utils.rate_limiter import get_rate_limiter_sync
from data_service.utils.hip3_mapping import (
    to_hip3_symbol, from_hip3_symbol, is_hip3_asset, get_dex_index, to_hip3_with_dex
)

logger = logging.getLogger(__name__)

@dataclass
class Asset:
    symbol: str
    name: str
    sz_decimals: int
    px_decimals: int
    max_leverage: int
    is_perp: bool = True

@dataclass
class MarketData:
    symbol: str
    mid_price: float
    bid: float
    ask: float
    day_volume: float
    open_interest: float
    timestamp: float

@dataclass
class OrderBookEntry:
    px: float
    sz: float
    n: int

@dataclass
class OrderBook:
    symbol: str
    levels: List[List[OrderBookEntry]]  # [bids, asks]
    timestamp: float

@dataclass
class FundingRate:
    symbol: str
    rate: float
    time: int

class MockPriceEngine:
    """
    Realistic price generator for mock mode.
    Features:
    - Asset-specific volatility profiles
    - Market hours simulation for stocks
    - Realistic spreads based on liquidity
    - Correlation between related assets
    """
    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.prices: Dict[str, float] = {}
        self.last_update: Dict[str, datetime] = {}

        # Initial prices for assets (realistic Feb 2026 prices)
        self.base_prices = {
            # Metals
            "XAU": 2850.0,    # Gold
            "XAG": 32.50,     # Silver
            "HG": 4.25,       # Copper
            "PLAT": 1050.0,   # Platinum
            # Stocks
            "TSLA": 420.0,
            "NVDA": 950.0,
            "AAPL": 245.0,
            "GOOGL": 195.0,
            "AMZN": 225.0,
            "META": 620.0,
            "MSFT": 480.0,
            "AMD": 175.0,
            "COIN": 285.0,
            # Crypto
            "BTC": 96000.0,   # Bitcoin
            "ETH": 2700.0,    # Ethereum
            # Commodities
            "CL": 78.0,       # Crude Oil
            "NG": 2.85        # Natural Gas
        }

        # Volatility profiles (daily volatility as decimal)
        self.volatility = {
            "XAU": 0.008,   "XAG": 0.015,   "HG": 0.012,   "PLAT": 0.014,
            "TSLA": 0.035,  "NVDA": 0.032,  "AAPL": 0.015, "GOOGL": 0.018,
            "AMZN": 0.020,  "META": 0.025,  "MSFT": 0.012, "AMD": 0.035,
            "COIN": 0.045,  "BTC": 0.030,   "ETH": 0.035,
            "CL": 0.022,    "NG": 0.040
        }

        # Spread in basis points (bid-ask spread)
        self.spreads_bps = {
            "XAU": 2,  "XAG": 5,  "HG": 8,  "PLAT": 10,
            "TSLA": 1, "NVDA": 1, "AAPL": 1, "GOOGL": 1,
            "AMZN": 1, "META": 1, "MSFT": 1, "AMD": 2,
            "COIN": 3, "BTC": 1,  "ETH": 2,
            "CL": 3,  "NG": 5
        }

        # US Market hours (EST): 9:30 AM - 4:00 PM
        self.stock_symbols = {"TSLA", "NVDA", "AAPL", "GOOGL", "AMZN", "META", "MSFT", "AMD", "COIN"}

    def _is_market_open(self, symbol: str) -> bool:
        """Check if market is open for this symbol."""
        if symbol not in self.stock_symbols:
            return True  # Metals/commodities trade 24/5

        now = datetime.now()
        # Simple check: weekday and between 14:30-21:00 UTC (9:30-16:00 EST)
        if now.weekday() >= 5:  # Weekend
            return False
        hour_utc = now.hour
        # Approximate US market hours in UTC
        return 14 <= hour_utc < 21

    def get_price(self, symbol: str) -> float:
        """Get current price with realistic movement."""
        now = datetime.now()

        if symbol not in self.prices:
            self.prices[symbol] = self.base_prices.get(symbol, 100.0)
            self.last_update[symbol] = now

        # Calculate time since last update
        last = self.last_update.get(symbol, now)
        elapsed_hours = (now - last).total_seconds() / 3600

        # Scale volatility by time elapsed (sqrt of time)
        vol = self.volatility.get(symbol, 0.02)
        time_factor = np.sqrt(max(elapsed_hours, 1/60) / 24)  # Normalize to daily

        # Reduced movement if market closed (for stocks)
        if not self._is_market_open(symbol):
            time_factor *= 0.1  # 90% reduction in movement

        # Generate price change
        change = self.rng.normal(0, vol * time_factor)
        self.prices[symbol] *= (1 + change)
        self.last_update[symbol] = now

        return self.prices[symbol]

    def get_spread(self, symbol: str) -> float:
        """Get bid-ask spread as a decimal."""
        bps = self.spreads_bps.get(symbol, 5)
        return bps / 10000

    def get_bid_ask(self, symbol: str) -> tuple:
        """Get bid and ask prices."""
        mid = self.get_price(symbol)
        spread = self.get_spread(symbol)
        half_spread = spread / 2
        return mid * (1 - half_spread), mid * (1 + half_spread)

    def get_candles(self, symbol: str, timeframe_secs: int, limit: int) -> pd.DataFrame:
        """Generate realistic OHLCV candles."""
        end_time = datetime.now()
        start_time = end_time - timedelta(seconds=timeframe_secs * limit)

        times = [int((start_time + timedelta(seconds=timeframe_secs * i)).timestamp() * 1000) for i in range(limit)]

        base_px = self.base_prices.get(symbol, 100.0)
        vol = self.volatility.get(symbol, 0.02)

        # Scale volatility to timeframe
        candle_vol = vol * np.sqrt(timeframe_secs / 86400)

        # Generate random walk with mean reversion
        rets = self.rng.normal(0, candle_vol, limit)
        # Add slight mean reversion
        for i in range(1, len(rets)):
            cumret = sum(rets[:i])
            rets[i] -= cumret * 0.01  # Pull back toward base

        prices = base_px * np.exp(np.cumsum(rets))

        # Generate OHLC from close prices
        opens = np.roll(prices, 1)
        opens[0] = base_px

        # High/low based on volatility
        intrabar_vol = candle_vol * 0.5
        highs = np.maximum(opens, prices) * (1 + np.abs(self.rng.normal(0, intrabar_vol, limit)))
        lows = np.minimum(opens, prices) * (1 - np.abs(self.rng.normal(0, intrabar_vol, limit)))

        # Volume varies with volatility
        base_volume = 1000000 if symbol in self.stock_symbols else 50000
        vol_factor = np.abs(rets) / candle_vol  # Higher volume on big moves
        volumes = base_volume * (0.5 + vol_factor) * self.rng.uniform(0.8, 1.2, limit)

        df = pd.DataFrame({
            'time': times,
            'open': opens,
            'high': highs,
            'low': lows,
            'close': prices,
            'volume': volumes
        })

        # Update current price to match latest candle
        self.prices[symbol] = prices[-1]

        return df

class HyperliquidFetcher:
    """
    Fetcher for Hyperliquid market data.
    Implements retry logic and mock mode.
    """
    def __init__(self, mode: Optional[str] = None):
        self.config = get_config()
        self.health = get_health()
        self.mode = mode or ("mock" if self.config.is_mock_mode() else "live")
        
        # HIP-3 Spot Asset Mapping (User-friendly symbol -> Token Name)
        self.spot_symbol_map = {
            "XAU": "GLD",
            "XAG": "SLV",
            "GOLD": "GLD",
            "SILVER": "SLV"
        }
        
        # Internal map: Token Name (GLD) -> Universe Name (@276)
        self.token_to_universe_map = {}

        if self.mode in ["live", "testnet"]:
            # Determine Base URL
            base_url = "https://api.hyperliquid.xyz"
            if self.mode == "testnet":
                base_url = "https://api.hyperliquid-testnet.xyz"

            # Initialize Info with HIP-3 DEX support
            hip3_dexs = ["xyz", "flx"]  # XYZ and Felix Exchange DEXes
            self.info = Info(base_url, skip_ws=True, perp_dexs=hip3_dexs)
            logger.info(f"HyperliquidFetcher initialized in {self.mode.upper()} mode ({base_url})")
            logger.info(f"Initialized with HIP-3 DEXes: {hip3_dexs}")
            
            # Load Spot Meta for HIP-3 assets (Mainnet only usually, but let's try safely)
            try:
                self.spot_meta = self.info.post("/info", {"type": "spotMeta"})
                self.spot_universe = {t['name']: t for t in self.spot_meta.get('universe', [])}
                self.spot_tokens = self.spot_meta.get('tokens', [])
                
                # Build map: Token Name -> Universe Name
                token_name_to_index = {t['name']: t['index'] for t in self.spot_tokens}
                
                for u in self.spot_meta.get('universe', []):
                    u_name = u['name']
                    u_tokens = u['tokens'] 
                    
                    for t_name, t_idx in token_name_to_index.items():
                        if t_idx in u_tokens:
                            self.token_to_universe_map[t_name] = u_name
                            
                logger.info(f"Mapped {len(self.token_to_universe_map)} Spot assets to Universe IDs")
                
            except Exception as e:
                logger.warning(f"Failed to load Spot Meta (Expected on Testnet): {e}")
                self.spot_universe = {}
                self.spot_tokens = []
                self.token_to_universe_map = {}

            # HIP-3 metas are loaded automatically by SDK when perp_dexs is specified

        else:
            self.mock_engine = MockPriceEngine()
            logger.info("HyperliquidFetcher initialized in MOCK mode")

    def _load_hip3_metas(self):
        """Load HIP-3 DEX metas into SDK so it can resolve HIP-3 asset names."""
        try:
            all_metas = self.info.post('/info', {'type': 'allPerpMetas'})

            # Calculate offset for each DEX and load HIP-3 metas
            offset = 0
            loaded_count = 0
            for dex_idx, dex_meta in enumerate(all_metas):
                universe_size = len(dex_meta.get('universe', []))
                if dex_idx > 0:  # Skip DEX 0 (main perps, already loaded)
                    self.info.set_perp_meta(dex_meta, offset)
                    loaded_count += universe_size
                offset += universe_size

            logger.info(f"Loaded {loaded_count} HIP-3 assets from {len(all_metas)-1} DEXes")
        except Exception as e:
            logger.warning(f"Failed to load HIP-3 metas: {e}")

    async def _retry_call(self, func, *args, timeout_override: Optional[float] = None, **kwargs):
        """
        Execute a function with exponential backoff retry logic and timeout enforcement.

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
        max_retries = 5
        base_delay = 1.0
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
                delay = base_delay * (2 ** i) + random.uniform(0, 0.1)
                logger.warning(f"Timeout retry {i+1}/{max_retries} after {delay:.2f}s")
                await asyncio.sleep(delay)

            except Exception as e:
                if i == max_retries - 1:
                    self.health.record_error()
                    logger.error(f"Final retry failed for {func.__name__}: {e}")
                    raise

                delay = base_delay * (2 ** i) + random.uniform(0, 0.1)
                logger.warning(f"Retry {i+1}/{max_retries} for {func.__name__} after {delay:.2f}s: {e}")
                await asyncio.sleep(delay)

    def _get_coin_name(self, symbol: str) -> str:
        """Resolve internal coin name for API calls using HIP-3 mapping.

        Maps internal symbols to HIP-3 format:
        - XAU -> flx:GOLD
        - TSLA -> xyz:TSLA
        - BTC -> BTC (unchanged for main perps)
        """
        # Use HIP-3 mapping utility
        hip3_symbol = to_hip3_symbol(symbol)
        logger.debug(f"Symbol resolution: {symbol} -> {hip3_symbol}")
        return hip3_symbol

    async def get_perpetuals_meta(self) -> List[Asset]:
        """Fetch metadata for all perpetual assets."""
        if self.mode == "mock":
            return [
                Asset(
                    symbol=a.symbol,
                    name=a.display_name,
                    sz_decimals=3,
                    px_decimals=2,
                    max_leverage=a.max_leverage
                ) for a in self.config.get_all_assets()
            ]
        
        # Live implementation
        meta = await self._retry_call(self.info.meta)
        assets = []
        for i, universe in enumerate(meta['universe']):
            assets.append(Asset(
                symbol=universe['name'],
                name=universe['name'],
                sz_decimals=meta['szDecimals'][i],
                px_decimals=6,
                max_leverage=50,
                is_perp=True
            ))
        return assets

    async def get_market_data(self, symbol: str) -> MarketData:
        """Fetch latest price, volume, and open interest for a symbol."""
        if self.mode == "mock":
            px = self.mock_engine.get_price(symbol)
            return MarketData(
                symbol=symbol,
                mid_price=px,
                bid=px * 0.9999,
                ask=px * 1.0001,
                day_volume=1000000.0,
                open_interest=500000.0,
                timestamp=time.time()
            )
        
        # Live implementation
        coin_name = self._get_coin_name(symbol)
        is_hip3 = is_hip3_asset(symbol)

        if is_hip3:
            # HIP-3 assets: Use direct API call (SDK l2_snapshot doesn't support HIP-3)
            logger.debug(f"Fetching HIP-3 market data for {symbol} as {coin_name}")
            try:
                # Use direct post for HIP-3 L2 book
                l2 = await self._retry_call(
                    self.info.post, '/info', {'type': 'l2Book', 'coin': coin_name}
                )

                # Get bid/ask from L2 book
                bids = l2.get('levels', [[]])[0]
                asks = l2.get('levels', [[], []])[1]

                if bids and asks:
                    bid = float(bids[0]['px'])
                    ask = float(asks[0]['px'])
                    px = (bid + ask) / 2  # Calculate mid from spread
                elif bids:
                    bid = float(bids[0]['px'])
                    ask = bid * 1.001
                    px = bid
                elif asks:
                    ask = float(asks[0]['px'])
                    bid = ask * 0.999
                    px = ask
                else:
                    logger.warning(f"No order book for HIP-3 asset {coin_name}")
                    return MarketData(
                        symbol=symbol, mid_price=0, bid=0, ask=0,
                        day_volume=0, open_interest=0, timestamp=time.time()
                    )

                return MarketData(
                    symbol=symbol,
                    mid_price=px,
                    bid=bid,
                    ask=ask,
                    day_volume=0.0,  # HIP-3 volume from different endpoint
                    open_interest=0.0,
                    timestamp=time.time()
                )
            except Exception as e:
                logger.error(f"Failed to fetch HIP-3 market data for {coin_name}: {e}")
                raise

        # Perps logic
        mdata_task = self._retry_call(self.info.meta_and_asset_ctxs)
        l2_task = self._retry_call(self.info.l2_snapshot, symbol)
        mids_task = self._retry_call(self.info.all_mids)
        
        mdata, l2, mids = await asyncio.gather(mdata_task, l2_task, mids_task)
        
        px = float(mids.get(symbol, 0))
        
        # Find asset context
        asset_ctx = {}
        for i, s in enumerate(mdata[0]['universe']):
            if s['name'] == symbol:
                asset_ctx = mdata[1][i]
                break

        return MarketData(
            symbol=symbol,
            mid_price=px,
            bid=float(l2['levels'][0][0]['px']),
            ask=float(l2['levels'][1][0]['px']),
            day_volume=float(asset_ctx.get('dayNtlVlm', 0)),
            open_interest=float(asset_ctx.get('openInterest', 0)),
            timestamp=time.time()
        )

    async def get_l2_book(self, symbol: str) -> OrderBook:
        """Fetch Level-2 order book snapshot."""
        if self.mode == "mock":
            px = self.mock_engine.get_price(symbol)
            half_spread = self.mock_engine.get_spread(symbol) / 2
            step = half_spread
            # Generate asymmetric volumes to produce realistic OBI
            # Use momentum from recent price changes to bias the book
            base_sz = 10.0
            imbalance = self.mock_engine.rng.normal(0, 0.3)  # random OBI bias
            # Clamp imbalance to [-0.6, 0.6]
            imbalance = max(-0.6, min(0.6, imbalance))
            bid_sz = base_sz * (1 + imbalance)   # more bids when positive
            ask_sz = base_sz * (1 - imbalance)   # more asks when negative
            bids = [OrderBookEntry(px * (1 - half_spread - step * i),
                                   bid_sz * self.mock_engine.rng.uniform(0.7, 1.3), 1)
                    for i in range(5)]
            asks = [OrderBookEntry(px * (1 + half_spread + step * i),
                                   ask_sz * self.mock_engine.rng.uniform(0.7, 1.3), 1)
                    for i in range(5)]
            return OrderBook(symbol=symbol, levels=[bids, asks], timestamp=time.time())

        # Live implementation
        coin_name = self._get_coin_name(symbol)

        # Use direct API for HIP-3 assets
        if is_hip3_asset(symbol):
            l2 = await self._retry_call(
                self.info.post, '/info', {'type': 'l2Book', 'coin': coin_name}
            )
        else:
            l2 = await self._retry_call(self.info.l2_snapshot, coin_name)

        bids = [OrderBookEntry(float(x['px']), float(x['sz']), int(x['n'])) for x in l2['levels'][0]]
        asks = [OrderBookEntry(float(x['px']), float(x['sz']), int(x['n'])) for x in l2['levels'][1]]
        return OrderBook(symbol=symbol, levels=[bids, asks], timestamp=time.time())

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Fetch OHLCV candles."""
        if self.mode == "mock":
            from data_service.utils.hyperliquid_helpers import timeframe_to_seconds
            t_secs = timeframe_to_seconds(timeframe)
            return self.mock_engine.get_candles(symbol, t_secs, limit)
        
        # Live implementation
        coin_name = self._get_coin_name(symbol)
        logger.debug(f"Fetching candles for {symbol} (resolved to {coin_name})")

        # HL supports: 1m, 5m, 15m, 1h, 4h, 1d
        end_time = int(time.time() * 1000)
        start_time = end_time - (limit * 1000 * 3600)  # Rough estimate for window assuming 1h
        if timeframe == '1m': start_time = end_time - (limit * 1000 * 60)
        elif timeframe == '5m': start_time = end_time - (limit * 1000 * 300)
        elif timeframe == '15m': start_time = end_time - (limit * 1000 * 900)
        elif timeframe == '4h': start_time = end_time - (limit * 1000 * 3600 * 4)
        elif timeframe == '1d': start_time = end_time - (limit * 1000 * 3600 * 24)

        # Use direct API for HIP-3 assets (SDK candles_snapshot doesn't support HIP-3)
        if is_hip3_asset(symbol):
            candles = await self._retry_call(
                self.info.post, '/info', {
                    'type': 'candleSnapshot',
                    'req': {
                        'coin': coin_name,
                        'interval': timeframe,
                        'startTime': start_time,
                        'endTime': end_time
                    }
                }
            )
        else:
            candles = await self._retry_call(self.info.candles_snapshot, coin_name, timeframe, start_time, end_time)
        df = pd.DataFrame(candles)
        if not df.empty:
            # Flexible column mapping (handle both 6-col and 10-col responses)
            # Standard keys: t, o, h, l, c, v
            rename_map = {
                't': 'time', 'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'
            }
            df.rename(columns=rename_map, inplace=True)
            
            # Ensure we have the required columns
            required_cols = ['time', 'open', 'high', 'low', 'close', 'volume']
            missing = [c for c in required_cols if c not in df.columns]
            if not missing:
                df = df[required_cols] # Keep only standard columns
                df = df.astype(float)
            else:
                logger.error(f"Missing columns in candle data for {symbol}: {missing}. Columns found: {df.columns.tolist()}")
                # Return empty if schema mismatch
                return pd.DataFrame()
        return df

    async def get_funding_history(self, symbol: str, days: int) -> List[FundingRate]:
        """Fetch historical funding rates."""
        if self.mode == "mock":
            now = int(time.time() * 1000)
            return [
                FundingRate(symbol=symbol, rate=0.0001, time=now - i * 3600000)
                for i in range(days * 24)
            ]
        
        # Live implementation
        coin_name = self._get_coin_name(symbol)

        # HIP-3 assets: funding history uses different endpoint or may not be available
        start_time = int((time.time() - days * 86400) * 1000)

        if is_hip3_asset(symbol):
            # Try direct API for HIP-3 funding
            try:
                funding = await self._retry_call(
                    self.info.post, '/info', {
                        'type': 'fundingHistory',
                        'coin': coin_name,
                        'startTime': start_time
                    }
                )
            except Exception as e:
                logger.debug(f"No funding history for HIP-3 asset {coin_name}: {e}")
                return []
        else:
            try:
                funding = await self._retry_call(self.info.funding_history, coin_name, start_time)
            except Exception as e:
                logger.warning(f"Failed to fetch funding for {coin_name}: {e}")
            return []
        return [
            FundingRate(symbol=symbol, rate=float(x['fundingRate']), time=int(x['time']))
            for x in funding
        ]
