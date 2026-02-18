"""
Unit tests for HyperliquidExecutor.
Focuses on mock mode to verify order tracking and position management.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock

from data_service.executors.hyperliquid_executor import HyperliquidExecutor, OrderResponse, Position, OpenOrder

@pytest.fixture
def executor():
    """Returns a HyperliquidExecutor in mock mode."""
    return HyperliquidExecutor(mode="mock")

@pytest.mark.asyncio
async def test_order_validation(executor):
    """Test order validation against asset config."""
    # Invalid side
    res = await executor.place_order("XAU", "hold", 0.1, px=None)
    assert not res.success
    assert "Invalid side" in res.error

    # Size too small (XAU min is usually 0.01 per config)
    # Check config/assets.json for exact value, assume 0.01
    res = await executor.place_order("XAU", "buy", 0.0001, px=None)
    assert not res.success
    assert "less than minimum" in res.error

    # Missing asset
    res = await executor.place_order("INVALID_COIN", "buy", 1.0, px=None)
    assert not res.success
    assert "not found in configuration" in res.error

@pytest.mark.asyncio
async def test_place_market_order(executor):
    """Test market order placement and position update in mock mode."""
    # Place a market buy order
    res = await executor.place_order("XAU", "buy", 0.1, px=None, order_type="market")
    assert res.success
    assert res.status == "filled"
    
    # Check position
    positions = await executor.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "XAU"
    assert positions[0].size == 0.1
    
    # Place a market sell order (closing half)
    res = await executor.place_order("XAU", "sell", 0.05, px=None, order_type="market")
    assert res.success
    
    positions = await executor.get_positions()
    assert len(positions) == 1
    assert positions[0].size == 0.05
    
    # Close position
    res = await executor.place_order("XAU", "sell", 0.05, px=None, order_type="market")
    positions = await executor.get_positions()
    assert len(positions) == 0

@pytest.mark.asyncio
async def test_place_limit_order(executor):
    """Test limit order placement and cancellation in mock mode."""
    # Place a limit buy order
    res = await executor.place_order("XAU", "buy", 0.1, px=2000.0, order_type="limit")
    assert res.success
    assert res.status == "open"
    assert res.order_id is not None
    
    # Check open orders
    orders = await executor.get_open_orders()
    assert len(orders) == 1
    assert orders[0].symbol == "XAU"
    assert orders[0].price == 2000.0
    
    # Cancel order
    cancelled = await executor.cancel_order("XAU", res.order_id)
    assert cancelled
    
    orders = await executor.get_open_orders()
    assert len(orders) == 0

@pytest.mark.asyncio
async def test_user_state(executor):
    """Test user state retrieval in mock mode."""
    state = await executor.get_user_state()
    assert state.equity == 10000.0
    assert state.available_margin == 10000.0

@pytest.mark.asyncio
async def test_fail_fast_logic():
    """Test that retry logic fails fast on margin errors."""
    executor = HyperliquidExecutor(mode="mock") # Mock ledger used but we want to test _retry_call
    
    # Create a mock function that raises a margin error
    mock_func = AsyncMock(side_effect=Exception("Insufficient Margin"))
    
    with pytest.raises(Exception) as excinfo:
        await executor._retry_call(mock_func)
    
    assert "Insufficient Margin" in str(excinfo.value)
    assert mock_func.call_count == 1 # Should NOT retry
