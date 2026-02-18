import asyncio
import logging
import sys
import time
from datetime import datetime
from data_service.ai.news_processor import NewsProcessor
from data_service.ai.sources.base_source import Article

# Setup logging to be minimal
logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

async def live_callback(article: Article):
    now = datetime.now()
    # Telegram messages have a date. We compare it to local time.
    # Note: This assumes local clock and Telegram clock are synced.
    lag = (now - article.published_at.replace(tzinfo=None)).total_seconds()
    print(f"[LIVE] Latency: {lag:.2f}s | Source: {article.source} | Title: {article.title[:60]}...")

async def run_latency_test():
    processor = NewsProcessor()
    symbols = ['XAU', 'GOLD', 'TSLA', 'BTC']
    
    print("=" * 60)
    print("PHASE 4.1 PERFORMANCE TEST: NEWS AGGREGATOR")
    print("=" * 60)
    
    # 1. Measure Polling Latency (Historical Fetch)
    print(f"\n[STEP 1] Measuring Polling Latency (Symbols: {symbols})...")
    start = time.time()
    articles = await processor.fetch_historical_news(symbols, 2)
    total_time = time.time() - start
    
    stats = processor.get_source_stats()
    print(f"\n[RESULTS] Polling Performance:")
    for src, data in stats.items():
        print(f"  - {src:15} | Avg Latency: {data['avg_latency_ms']:8.2f}ms | Calls: {data['calls']}")
    
    print(f"\nTotal batch fetch time: {total_time:.2f}s for {len(articles)} unique articles.")

    # 2. Live Stream Demo (Short run)
    print(f"\n[STEP 2] Starting Live Stream Demo (Running for 30 seconds)...")
    print("Watching for real-time updates from Telegram/Scrapers...")
    
    # We start the processor loop
    try:
        # We wrap the start in a timeout to only run the demo session for a bit
        await asyncio.wait_for(processor.start(live_callback), timeout=30.0)
    except asyncio.TimeoutError:
        print("\n[INFO] Live demo timed out as planned.")
    except Exception as e:
        print(f"\n[ERROR] Live demo error: {e}")

    print("\n" + "=" * 60)
    print("PERFORMANCE TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_latency_test())
