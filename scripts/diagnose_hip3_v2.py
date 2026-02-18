import asyncio
import logging
from data_service.executors.hyperliquid_executor import HyperliquidExecutor
from data_service.utils.hip3_mapping import to_hip3_symbol

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_hip3():
    executor = HyperliquidExecutor()
    print(f"--- Executor Mode: {executor.mode} ---")
    
    symbols = ["AMD", "NVDA", "XAU"]
    for sym in symbols:
        hip3 = to_hip3_symbol(sym)
        print(f"\nAsset: {sym} -> {hip3}")
        
        # 1. Check size decimals
        sz_dec = executor._get_sz_decimals(hip3)
        print(f"Size Decimals: {sz_dec}")
        
        # 2. Check mid price fetch (testing fallback)
        try:
            # We mock px=None to trigger mid price lookup in place_order's logic block
            # But we won't call place_order; we'll simulate the lookup logic.
            mids = executor.info.all_mids()
            mid = float(mids.get(hip3, 0)) or float(mids.get(sym, 0))
            print(f"SDK all_mids price: {mid}")
            
            if mid == 0:
                print("Triggering fallback L2 book lookup...")
                l2 = await executor._retry_call(executor.info.post, '/info', {'type': 'l2Book', 'coin': hip3})
                bids = l2.get('levels', [[]])[0]
                asks = l2.get('levels', [[], []])[1]
                if bids and asks:
                    mid = (float(bids[0]['px']) + float(asks[0]['px'])) / 2
                    print(f"Fallback L2 mid price: {mid}")
                else:
                    print("Fallback L2 book empty.")
        except Exception as e:
            print(f"Price fetch error: {e}")

if __name__ == "__main__":
    asyncio.run(test_hip3())
