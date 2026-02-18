import asyncio
import logging
import sys
from data_service.ai.sources.google_rss_source import GoogleRSSSource

# Setup logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

async def main():
    config = {
        'fetch_interval_minutes': 5
    }
    
    print("Initializing Google RSS Source...")
    source = GoogleRSSSource(config)
    
    print("\n--- Testing Fetch for 'XAU OR GOLD' ---")
    try:
        articles = await source.fetch_news(['XAU', 'GOLD'], 24)
        print(f"\n[SUCCESS] Fetched {len(articles)} articles.")
        
        for i, article in enumerate(articles[:5]):
            print(f"\n[{i+1}] {article.title}")
            print(f"    Source: {article.source}")
            print(f"    Time: {article.published_at}")
            print(f"    URL: {article.url}")
            
    except Exception as e:
        print(f"\n[ERROR] Fetch failed: {e}")

    print("\nTest Complete.")

if __name__ == "__main__":
    asyncio.run(main())
