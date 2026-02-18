import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher
from data_service.strategies.momentum_perpetuals import MomentumPerpetuals
from data_service.risk.risk_manager import RiskManager
from data_service.risk.position_sizer import PositionSizer
from data_service.executors.order_manager import OrderManager
from data_service.executors.hyperliquid_executor import HyperliquidExecutor
from data_service.storage.database_manager import DatabaseManager
from data_service.storage.order_storage import OrderStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("E2E_MockRun")

async def run_e2e():
    logger.info("🚀 Starting E2E Mock Run (Phase 9)...")
    
    # 1. Initialize Storage
    db = DatabaseManager()
    order_storage = OrderStorage()
    
    # 2. Initialize Components
    fetcher = HyperliquidFetcher(mode="mock")
    strategy = MomentumPerpetuals()
    risk_mgr = RiskManager(db_manager=db)
    pos_sizer = PositionSizer(risk_manager=risk_mgr)
    executor = HyperliquidExecutor(mode="mock")
    order_mgr = OrderManager(executor=executor, storage=order_storage)
    
    # Setup initial state
    risk_mgr.set_portfolio(equity=100_000, open_positions=[])
    pos_sizer.set_equity(100_000)
    
    symbols = ["XAU", "ETH"]
    
    # 3. Simulated Loop (3 iterations)
    for i in range(1, 4):
        logger.info(f"--- Iteration {i} ---")
        
        # A. Fetch data (mock)
        market_data = {}
        for sym in symbols:
            df = await fetcher.get_candles(sym, timeframe='1h', limit=200)
            market_data[sym] = df
        logger.info(f"Fetched data for {list(market_data.keys())}")
        
        # B. Calculate Signals
        factors = {'fetcher': fetcher}
        signals = await strategy.calculate_signals(market_data, factors)
        
        # C. Size Positions
        # For E2E test, we'll force a signal if none exists to test the full pipeline
        if all(s.direction == 'flat' for s in signals.values()):
            logger.info("Forcing a 'long' signal for XAU to test E2E pipeline...")
            from data_service.strategies.strategy_base import Signal
            signals["XAU"] = Signal("XAU", "long", 0.8, "E2E Force Signal")
            
        target_positions = strategy.size_positions(signals, None)
        logger.info(f"Target positions: {target_positions}")
        
        # D. Pre-trade Risk Checks & Sizer constraints
        # (This is usually integrated in strategy or higher level controller)
        for sym, target_pct in target_positions.items():
            if target_pct == 0: continue
            
            px = market_data[sym]['close'].iloc[-1]
            raw_size = abs(target_pct * 100_000 / px)
            
            final_size = pos_sizer.apply_constraints(
                symbol=sym,
                raw_size=raw_size,
                leverage=3.0,
                price=px,
                min_order_size=0.001
            )
            
            if final_size > 0:
                side = "buy" if target_pct > 0 else "sell"
                logger.info(f"Risk Check Passed: {sym} {side} size={final_size:.4f}")
                
                # E. Order Execution
                res = await order_mgr.create_order(
                    symbol=sym,
                    side=side,
                    sz=final_size,
                    px=px,
                    strategy_name="e2e_mock"
                )
                logger.info(f"Order Result: {res}")
        
        # F. Risk Snapshot
        snapshot = risk_mgr.get_risk_snapshot()
        db.save_risk_snapshot(snapshot)
        logger.info(f"Risk Snapshot saved: {snapshot['total_equity']}")
        
        await asyncio.sleep(1) # Simulate time passing
        
    logger.info("✅ E2E Mock Run Completed Successfully!")
    
    # 4. Final Verification
    history = order_mgr.get_order_history()
    logger.info(f"Final Order History Count: {len(history)}")
    assert len(history) > 0, "No orders were generated in history"
    
    snapshots = db.get_recent_risk_snapshots(limit=5)
    logger.info(f"Recent Risk Snapshots Count: {len(snapshots)}")
    assert len(snapshots) >= 3, "Not all risk snapshots were saved"

if __name__ == "__main__":
    asyncio.run(run_e2e())
