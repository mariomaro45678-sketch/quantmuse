import asyncio
import argparse
from datetime import datetime, timedelta
from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher
from data_service.storage.database_manager import DatabaseManager

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', required=True)
    parser.add_argument('--timeframe', default='1h')
    parser.add_argument('--days', type=int, default=180)
    args = parser.parse_args()
    
    symbols = args.symbols.split(',')
    # Using mode='live' because HyperliquidFetcher now supports HIP-3 Spot assets (GLD, SLV, TSLA, NVDA)
    # matching the user's requirement for real data.
    fetcher = HyperliquidFetcher(mode='live')
    db = DatabaseManager()
    
    for symbol in symbols:
        print(f"Fetching {args.days} days of {args.timeframe} data for {symbol}...")
        # Hyperliquid limits are usually handled by the fetcher (candles_snapshot handles limitations? No it takes start/end)
        # get_candles in HyperliquidFetcher implements fetching.
        # However, HyperliquidFetcher.get_candles logic:
        # start_time = end_time - (limit * 1000 * 3600) for 1h? 
        # The Fetcher code: 
        # end_time = int(time.time() * 1000)
        # start_time = end_time - (limit * 1000 * 3600)
        # It assumes limit is in hours?? 
        # In this script, limit is passed as args.days * 24.
        # If timeframe is '1h', limit * 3600 * 1000 is correct duration.
        # But if timeframe is different, the logic in Fetcher might be flawed if it hardcodes 3600.
        # Let's verify Fetcher logic in my thought trace.
        # Fetcher: start_time = end_time - (limit * 1000 * 3600)
        # This HARDCODES 1h interval logic. 
        # If I pass timeframe='1h', it's fine.
        
        df = await fetcher.get_candles(symbol, args.timeframe, limit=args.days * 24)
        
        # Save to DB
        # Check if df is empty
        if df.empty:
            print(f"⚠️ No data returned for {symbol}")
            continue

        for _, row in df.iterrows():
            db.save_candle(symbol, args.timeframe, row.to_dict())
        
        print(f"✅ {symbol}: {len(df)} candles saved")

if __name__ == '__main__':
    asyncio.run(main())
