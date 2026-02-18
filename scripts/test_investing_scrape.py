import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
from data_service.ai.sources.investing_com_source import InvestingComSource

# Setup logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

async def main():
    load_dotenv()
    
    config = {
        'base_url': 'https://www.investing.com/news',
        'intervals_seconds': [5, 10], # Short interval for test
        'use_proxies': True
    }
    
    print("Initializing Investing.com Scraper...")
    source = InvestingComSource(config)
    
    print("\n[INFO] Active Proxy:", source.current_proxy)
    
    print("\n--- Testing Proxy Connectivity (httpbin.org) ---")
    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, source.scraper.get, "https://httpbin.org/ip")
        print(f"[CONNECTIVITY CHECK] Status: {resp.status_code}")
        print(f"[CONNECTIVITY CHECK] IP: {resp.json()}")
    except Exception as e:
        print(f"[CONNECTIVITY CHECK] FAILED: {e}")

    print("\n--- Testing Single Fetch ---")

    try:
        articles = await source.fetch_news([], 1)
        print(f"\n[SUCCESS] Fetched {len(articles)} articles.")
        
        for i, article in enumerate(articles[:5]):
            print(f"\n[{i+1}] {article.title}")
            print(f"    URL: {article.url}")
            print(f"    Time: {article.published_at}")
            
    except Exception as e:
        print(f"\n[ERROR] Fetch failed: {e}")

    print("\nTest Complete.")

if __name__ == "__main__":
    asyncio.run(main())
