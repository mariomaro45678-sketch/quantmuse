"""
Integration tests for OrderManager.
Sequences a full trade lifecycle using the MockLedger and Storage.
"""

import pytest
import asyncio
from data_service.executors.order_manager import OrderManager
from data_service.executors.hyperliquid_executor import HyperliquidExecutor
from data_service.storage.order_storage import OrderStorage

@pytest.mark.asyncio
async def test_full_trade_lifecycle_integration(tmp_path):
    """
    Sequence an entire trade: 
    Manager calls Executor (mock) to open -> confirmed -> Manager calls Executor to close -> confirmed.
    """
    db_path = tmp_path / "integration_orders.db"
    storage = OrderStorage(db_path=db_path)
    # Using real executor but in mock mode
    executor = HyperliquidExecutor(mode="mock")
    manager = OrderManager(executor=executor, storage=storage)
    
    symbol = "XAU"
    
    # 1. Entry: Place limit buy
    res_entry = await manager.create_order(symbol, "buy", 0.01, px=40000.0, strategy_name="trend_strategy")
    assert res_entry.success
    oid = res_entry.order_id
    
    # Verify it's in active_orders
    assert oid in manager.active_orders
    
    # 2. Simulate Fill (normally handled by sync_orders or similar)
    # For this test, we'll manually simulate the status change as if it filled
    manager.active_orders[oid]["status"] = "filled"
    manager.active_orders[oid]["fill_price"] = 40000.0
    manager.storage.save_order(manager.active_orders[oid])
    
    # 3. Exit: Place market sell to close
    res_exit = await manager.create_order(symbol, "sell", 0.01, px=None, order_type="market", strategy_name="trend_strategy")
    assert res_exit.success
    assert res_exit.status == "filled"
    
    # Note: In our current simple MockLedger, market orders fill immediately and don't stay in active_orders.
    # The entry order (limit) is still technically "active" in our manager until we manually clear or sync it.
    # In a real system, sync_orders would move it to history.
    
    # 4. Cleanup/Close active entry order
    # If we "cancel" it after it's filled, it just moves to history in our current implementation
    await manager.cancel_order(symbol, oid)
    
    # 5. Check history
    history = manager.get_order_history()
    # Should have 2 entries: the entry buy (now cancelled/closed) and the exit sell
    assert len(history) == 2
    
    # Verify persistence
    assert any(h["symbol"] == symbol and h["side"] == "buy" for h in history)
    assert any(h["symbol"] == symbol and h["side"] == "sell" for h in history)
