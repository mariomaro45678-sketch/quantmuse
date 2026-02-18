"""
Unit tests for OrderManager and OrderStorage.
"""

import pytest
import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime

from data_service.executors.order_manager import OrderManager
from data_service.executors.hyperliquid_executor import HyperliquidExecutor, OrderResponse
from data_service.storage.order_storage import OrderStorage

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_orders.db"

@pytest.fixture
def storage(db_path):
    return OrderStorage(db_path=db_path)

@pytest.fixture
def mock_executor():
    executor = MagicMock(spec=HyperliquidExecutor)
    executor.place_order = AsyncMock()
    executor.cancel_order = AsyncMock()
    return executor

@pytest.fixture
def manager(mock_executor, storage):
    return OrderManager(executor=mock_executor, storage=storage)

@pytest.mark.asyncio
async def test_create_and_persist_order(manager, mock_executor, storage):
    """Test creating an order and ensuring it's persisted."""
    oid = 12345
    mock_executor.place_order.return_value = OrderResponse(
        success=True, order_id=oid, status="open"
    )
    
    res = await manager.create_order("XAU", "buy", 0.1, px=2000.0, strategy_name="trend_follower")
    
    assert res.success
    assert res.order_id == oid
    
    # Check memory tracking
    assert oid in manager.active_orders
    assert manager.active_orders[oid]["symbol"] == "XAU"
    
    # Check storage
    history = storage.get_history()
    assert len(history) == 1
    assert history[0]["order_id"] == oid
    assert history[0]["strategy_name"] == "trend_follower"

@pytest.mark.asyncio
async def test_cancel_order_lifecycle(manager, mock_executor, storage):
    """Test cancelling an order and updating its status in storage."""
    oid = 555
    # 1. Create order
    mock_executor.place_order.return_value = OrderResponse(
        success=True, order_id=oid, status="open"
    )
    await manager.create_order("XAU", "buy", 0.1, px=2000.0)
    
    # 2. Cancel order
    mock_executor.cancel_order.return_value = True
    success = await manager.cancel_order("XAU", oid)
    
    assert success
    assert oid not in manager.active_orders
    
    # 3. Verify status in database
    history = storage.get_history()
    assert history[0]["status"] == "cancelled"
    assert history[0]["closed_at"] is not None

def test_storage_initialization(db_path):
    """Test that the database is initialized correctly."""
    storage = OrderStorage(db_path=db_path)
    assert os.path.exists(db_path)
    
    history = storage.get_history()
    assert isinstance(history, list)
    assert len(history) == 0
