import asyncio
import sqlite3
import logging
from datetime import datetime
from data_service.executors.hyperliquid_executor import HyperliquidExecutor
from data_service.storage.database_manager import DatabaseManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BackfillPnL")

async def main():
    ex = HyperliquidExecutor()
    db = DatabaseManager()
    
    logger.info("Fetching all user fills from Hyperliquid...")
    fills = await ex.get_user_fills()
    if not fills:
        logger.info("No fills found.")
        return
        
    logger.info(f"Retrieved {len(fills)} fills from exchange.")
    
    fills_by_oid = {}
    for f in fills:
        oid = f.get('oid')
        if oid:
            if oid not in fills_by_oid:
                fills_by_oid[oid] = []
            fills_by_oid[oid].append(f)
            
    logger.info("Connecting to database...")
    conn = db._connect()
    cursor = conn.cursor()
    
    # Get all trades
    cursor.execute("SELECT id, order_id, status FROM trades WHERE status != 'cancelled'")
    trades_rows = cursor.fetchall()
    logger.info(f"Found {len(trades_rows)} trades in database.")
    
    updated = 0
    # Update in massive batches but it's only 2000 rows so memory/time is fine
    for row in trades_rows:
        db_id, oid, status = row
        if oid in fills_by_oid:
            trade_fills = fills_by_oid[oid]
            
            # Aggregate fill data
            total_sz = sum(float(f['sz']) for f in trade_fills)
            avg_px = sum(float(f['px']) * float(f['sz']) for f in trade_fills) / total_sz if total_sz > 0 else 0
            exchange_pnl = sum(float(f.get('closedPnl', 0)) for f in trade_fills)
            
            cursor.execute("""
                UPDATE trades 
                SET fill_price = ?, status = 'filled', realized_pnl = ?
                WHERE id = ?
            """, (avg_px, exchange_pnl, db_id))
            
            updated += 1
            
    conn.commit()
    conn.close()
    logger.info(f"Successfully updated {updated} trades with correct P&L.")
    
if __name__ == "__main__":
    asyncio.run(main())
