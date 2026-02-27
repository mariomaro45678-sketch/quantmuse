import asyncio
import os
from dotenv import load_dotenv

from data_service.utils.config_loader import ConfigLoader
from data_service.executors.hyperliquid_executor import HyperliquidExecutor

async def main():
    load_dotenv('.env')
    
    private_key = os.environ.get('HYPERLIQUID_API_SECRET')
    account = os.environ.get('HYPERLIQUID_API_KEY')
    if not private_key:
        print('No private key found')
        return

    ConfigLoader()
        
    executor = HyperliquidExecutor(mode='live')
    state = await executor.get_user_state()
    print('Attributes of UserState:', dir(state))
    print('State vars:', vars(state))
    
    positions = await executor.get_positions()
    print(f'\nTotal API positions: {len(positions)}')
    for pos in positions:
        print(f"{getattr(pos, 'asset', pos)}: {getattr(pos, 'side', '')} {getattr(pos, 'size', '')} @ {getattr(pos, 'entry_price', '')} (uPnl: {getattr(pos, 'unrealized_pnl', getattr(pos, 'pnl', ''))})")

if __name__ == '__main__':
    asyncio.run(main())
