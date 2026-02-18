import asyncio
import logging
from pathlib import Path
from datetime import datetime
from data_service.executors.hyperliquid_executor import HyperliquidExecutor
from data_service.executors.order_manager import OrderManager
from data_service.storage.order_storage import OrderStorage
from data_service.utils.config_loader import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_reconciliation():
    # Force mock mode and use a temporary DB for clean test
    db_path = Path("test_reconcile.db")
    if db_path.exists():
        db_path.unlink()
        
    executor = HyperliquidExecutor(mode="mock")
    storage = OrderStorage(db_path=db_path)
    order_mgr = OrderManager(executor=executor, storage=storage)
    
    print("\n--- [1] Creating Test Trade ---")
    symbol = "XAU"
    # Place a market order (it will be immediately filled in mock mode)
    res = await order_mgr.create_order(symbol, "buy", 0.01, px=None, strategy_name="test_reconcile")
    
    if res.success:
        print(f"Order {res.order_id} created and marked as {res.status}")
        
        # In mock mode, the fill is generated immediately but the trade record
        # created in create_order initially might have 0 P&L until reconciled.
        
        # Check storage record
        history = storage.get_history(limit=1)
        trade = history[0]
        print(f"Initial Trade Record: ID={trade['order_id']}, PnL={trade['realized_pnl']}")
        
        print("\n--- [2] Running Reconciliation ---")
        await order_mgr.sync_orders()
        
        # Check storage record again
        history = storage.get_history(limit=1)
        trade = history[0]
        print(f"Reconciled Trade Record: ID={trade['order_id']}, PnL={trade['realized_pnl']}, Fill Px={trade['fill_price']}")
        
        if trade['realized_pnl'] is not None:
            print("✅ P&L Reconciliation Successful!")
        else:
            print("❌ P&L Reconciliation Failed (still None/NULL)")
    else:
        print(f"Order placement failed: {res.error}")

if __name__ == "__main__":
    asyncio.run(test_reconciliation())
