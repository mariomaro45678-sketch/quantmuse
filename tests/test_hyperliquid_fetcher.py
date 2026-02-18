"""
Unit tests for HyperliquidFetcher.
Focuses on mock mode to verify data structures and logic without network calls.
"""

import pytest
import pandas as pd
import numpy as np
import asyncio
from unittest.mock import MagicMock, AsyncMock

from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher, Asset, MarketData, OrderBook, FundingRate

@pytest.fixture
def fetcher():
    """Returns a HyperliquidFetcher in mock mode."""
    return HyperliquidFetcher(mode="mock")

@pytest.mark.asyncio
async def test_get_perpetuals_meta(fetcher):
    """Test metadata retrieval in mock mode."""
    meta = await fetcher.get_perpetuals_meta()
    assert isinstance(meta, list)
    assert len(meta) > 0
    assert isinstance(meta[0], Asset)
    assert meta[0].symbol == "XAU"

@pytest.mark.asyncio
async def test_get_market_data(fetcher):
    """Test market data retrieval in mock mode."""
    mdata = await fetcher.get_market_data("XAU")
    assert isinstance(mdata, MarketData)
    assert mdata.symbol == "XAU"
    assert mdata.mid_price > 0
    assert mdata.bid < mdata.mid_price < mdata.ask
    assert mdata.timestamp > 0

@pytest.mark.asyncio
async def test_get_l2_book(fetcher):
    """Test L2 order book retrieval in mock mode."""
    book = await fetcher.get_l2_book("XAU")
    assert isinstance(book, OrderBook)
    assert len(book.levels[0]) == 5 # 5 bids
    assert len(book.levels[1]) == 5 # 5 asks
    assert book.levels[0][0].px < book.levels[1][0].px # Bid < Ask

@pytest.mark.asyncio
async def test_get_candles(fetcher):
    """Test candle retrieval in mock mode."""
    limit = 100
    df = await fetcher.get_candles("XAU", "1h", limit)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == limit
    assert list(df.columns) == ['time', 'open', 'high', 'low', 'close', 'volume']
    assert not df.isnull().values.any()

@pytest.mark.asyncio
async def test_get_funding_history(fetcher):
    """Test funding history retrieval in mock mode."""
    days = 7
    funding = await fetcher.get_funding_history("XAU", days)
    assert isinstance(funding, list)
    assert len(funding) == days * 24
    assert isinstance(funding[0], FundingRate)
    assert funding[0].rate == 0.0001

@pytest.mark.asyncio
async def test_retry_logic():
    """Test retry logic calling a failing function."""
    fetcher = HyperliquidFetcher(mode="mock")
    
    mock_func = AsyncMock(side_effect=[Exception("Fail 1"), Exception("Fail 2"), "Success"])
    
    # We need to mock health_check to avoid side effects
    fetcher.health = MagicMock()
    
    # Reduce delay for testing
    import data_service.fetchers.hyperliquid_fetcher as hf
    # hf.asyncio.sleep = AsyncMock() # Mock sleep to speed up test
    
    result = await fetcher._retry_call(mock_func)
    
    assert result == "Success"
    assert mock_func.call_count == 3
    assert fetcher.health.record_api_call.call_count == 3

@pytest.mark.asyncio
async def test_mock_price_engine_determinism():
    """Verify that multiple fetchers with same seed/state yield same initial prices if intended."""
    # Note: Currently MockPriceEngine doesn't take a seed in Fetcher init, but we can check if it behaves reasonably
    f1 = HyperliquidFetcher(mode="mock")
    f2 = HyperliquidFetcher(mode="mock")
    
    p1 = (await f1.get_market_data("XAU")).mid_price
    p2 = (await f2.get_market_data("XAU")).mid_price
    
    # Since they share default engine seed but might evolve independently if called differently.
    # Actually they create separate MockPriceEngine instances.
    assert p1 == p2 # Should be same initial base price
