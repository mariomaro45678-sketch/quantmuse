import asyncio
import logging
import os
from dotenv import load_dotenv
from data_service.ai.sources.telegram_source import TelegramSource

# Setup basic logging
logging.basicConfig(level=logging.INFO)

async def main():
    # Load env vars
    load_dotenv()
    
    config = {
        'api_id': int(os.getenv('TELEGRAM_API_ID')),
        'api_hash': os.getenv('TELEGRAM_API_HASH'),
        'phone': os.getenv('TELEGRAM_PHONE'),
        'channels': ["@WalterBloomberg", "@fxstreetforex", "@bloomberg"], 

        'keywords': ["GOLD", "XAU", "SILVER", "XAG", "USD", "FED", "CPI", "MARKET", "STOCK", "RATE", "TRUMP", "BITCOIN", "BTC"] 
    }
    
    print(f"Testing Telegram Source...")
    source = TelegramSource(config)
    
    # Initialize client first to debug entities
    await source._init_client()
    client = source.client
    
    print("\n--- Debugging Channel Resolution ---")
    for channel in config['channels']:
        try:
            entity = await client.get_entity(channel)
            print(f"✅ Resolved {channel}: {entity.title} (ID: {entity.id})")
        except Exception as e:
            print(f"❌ Failed to resolve {channel}: {e}")

    print("\n--- Fetching History (Broad Keywords) ---")
    # Pass all config keywords to fetch_news to increase hit rate
    articles = await source.fetch_news(config['keywords'], 24)
    
    print(f"Found {len(articles)} relevant articles.")
    for i, article in enumerate(articles[:10]):
        print(f"\n[{i+1}] {article.title}")
        print(f"    Source: {article.source}")
        print(f"    Time: {article.published_at}")

    print("\nTest Complete.")

if __name__ == "__main__":
    asyncio.run(main())
