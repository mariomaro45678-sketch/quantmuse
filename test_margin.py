import asyncio
from data_service.executors.hyperliquid_executor import HyperliquidExecutor

async def main():
    executor = HyperliquidExecutor(mode="live")
    state = await executor.get_user_state()
    print(f"Equity: ${state.equity:.2f}")
    print(f"Available Margin: ${state.available_margin:.2f}")
    print(f"Initial Margin Used: ${state.initial_margin_used:.2f}")
    for dex in [None, 'xyz', 'flx']:
        try:
            payload = {'type': 'clearinghouseState', 'user': executor.address}
            if dex: payload['dex'] = dex
            state = await executor._retry_call(executor.info.post, '/info', payload)
            margin = state.get('marginSummary', {})
            val = float(margin.get('accountValue', 0))
            avail = float(state.get('withdrawable', 0))
            print(f"DEX {dex or 'main'}: Equity=${val:.2f}, Available=${avail:.2f}")
        except Exception as e:
            print(f"DEX {dex or 'main'}: Error {e}")
            
    positions = await executor.get_positions()
    total_exposure = sum(abs(p.size * p.entry_price) for p in positions)
    print(f"Total Exposure: ${total_exposure:.2f} ({total_exposure/state.equity*100 if state.equity else 0:.1f}%)")

if __name__ == "__main__":
    asyncio.run(main())
