import asyncio
from data_service.executors.hyperliquid_executor import HyperliquidExecutor

async def main():
    executor = HyperliquidExecutor(mode="live")
    
    # Try to transfer $1 from main to xyz
    print("Transferring $1.00 from main to xyz DEX...")
    try:
        res = await executor._retry_call(
            executor.exchange.send_asset,
            destination=executor.address,
            source_dex="",
            destination_dex="xyz",
            token="USDC",
            amount=1.0
        )
        print("Result:", res)
    except Exception as e:
        print("Transfer failed:", e)

if __name__ == "__main__":
    asyncio.run(main())
