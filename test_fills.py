import asyncio
from data_service.executors.hyperliquid_executor import HyperliquidExecutor

async def main():
    ex = HyperliquidExecutor()
    fills = await ex.get_user_fills()
    if fills:
        for fill in fills[:5]:
            print(fill)
    else:
        print("No fills")

asyncio.run(main())
