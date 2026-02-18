import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.risk.risk_manager import RiskManager
from data_service.storage.database_manager import DatabaseManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RiskValidation")

async def verify_risk():
    logger.info("🛡️ Starting Codified Risk Validation...")
    db = DatabaseManager()
    rm = RiskManager(db_manager=db)
    rm.set_portfolio(equity=100_000, open_positions=[], session_high_equity=100_000)
    
    # --- 1. Leverage Block Test ---
    logger.info("Testing leverage block...")
    # Try an order that exceeds 5x portfolio leverage ($500,001)
    res = rm.pre_trade_check(symbol="XAU", size=10.0, leverage=30.0, price=20000.0)
    assert not res.approved, "Leverage check should have failed for excessive order"
    assert "leverage" in res.reason.lower()
    logger.info("✅ Leverage block verified.")
    
    # --- 2. Circuit Breaker Test ---
    logger.info("Testing circuit breaker...")
    # Default CB in config is 15% drawdown
    # Simulate equity drop from 100k to 80k (20% drawdown)
    fired = rm.on_equity_update(80_000)
    assert fired == True, "Circuit breaker should have fired at 20% drawdown"
    
    # Verify alert was persisted
    alerts = db.get_recent_alerts(limit=5)
    assert any(a['type'] == 'circuit_breaker' for a in alerts), "CB alert not found in database"
    logger.info("✅ Circuit breaker and alert persistence verified.")
    
    # --- 3. Daily Loss Gate Test ---
    logger.info("Testing daily loss gate...")
    rm.session_start_equity = 100_000
    rm.set_daily_pnl(-11_000) # -11% (exceeds 10% limit)
    res = rm.pre_trade_check("XAU", 1.0, 1.0, 2000.0)
    assert not res.approved, "Daily loss gate should have blocked order"
    assert "daily loss" in res.reason.lower()
    logger.info("✅ Daily loss gate verified.")
    
    logger.info("🎯 All risk validation checks PASSED!")

if __name__ == "__main__":
    asyncio.run(verify_risk())
