"""
Standalone Integration Test - Verifies Hyperliquid connection, fetching, and order placement.
"""

import sys
import asyncio
import logging
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent))

from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher
from data_service.executors.hyperliquid_executor import HyperliquidExecutor
from data_service.utils.config_loader import get_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def run_test():
    print("🚀 Starting Hyperliquid Connection Test...\n")

    # 1. Load Config
    config = get_config()
    mode = "MOCK" if config.is_mock_mode() else "LIVE"
    print(f"📡 Mode: {mode}")

    # 2. Instantiate Fetcher
    print("\n--- [1] Fetcher Integration ---")
    fetcher = HyperliquidFetcher()
    
    # 3. Print Perpetuals Meta
    print("⏳ Retrieving perpetuals metadata...")
    meta = await fetcher.get_perpetuals_meta()
    print(f"✅ Retrieved {len(meta)} assets. First 3: {[a.symbol for a in meta[:3]]}")

    # 4. Fetch 100 candles for XAU
    symbol = "XAU"
    print(f"⏳ Fetching 100 candles for {symbol} (1h)...")
    candles = await fetcher.get_candles(symbol, "1h", limit=100)
    print(f"✅ Received {len(candles)} candles. Latest Price: {candles['close'].iloc[-1]}")

    # 5. Instantiate Executor
    print("\n--- [2] Executor Integration ---")
    executor = HyperliquidExecutor()

    # 6. Place a tiny limit order
    # Note: Use a price far from market if live to avoid immediate fill
    test_px = 1000.0 if symbol == "XAU" else 10000.0 
    print(f"⏳ Placing tiny limit order for {symbol} at {test_px}...")
    
    # Check if credentials are placeholders
    if mode == "LIVE":
        print("⚠️ Running in LIVE mode. Placing real testnet order...")
    else:
        print("ℹ️ Running in MOCK mode. Simulation fill check...")

    res = await executor.place_order(symbol, "buy", 0.01, px=test_px, order_type="limit")
    
    if res.success:
        print(f"✅ Order placed successfully! Order ID: {res.order_id}")
        
        # 7. Cancel it immediately
        print(f"⏳ Cancelling order {res.order_id}...")
        cancelled = await executor.cancel_order(symbol, res.order_id)
        if cancelled:
            print("✅ Order cancelled successfully.")
        else:
            print("❌ Failed to cancel order.")
    else:
        print(f"❌ Order placement failed: {res.error}")

    print("\n🏁 Integration test complete.")

if __name__ == "__main__":
    asyncio.run(run_test())
