import asyncio
import logging
import sys
from datetime import datetime
from data_service.ai.news_processor import NewsProcessor
from data_service.ai.sources.base_source import Article

# Setup logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

async def test_callback(article: Article):
    print(f"\n[EVENT] New Article Received:")
    print(f"    Title: {article.title}")
    print(f"    Source: {article.source}")
    print(f"    ID: {article.id}")

async def main():
    print("Initializing News Aggregator...")
    processor = NewsProcessor()
    
    symbols = ['XAU', 'GOLD', 'TSLA']
    hours_back = 24
    
    print(f"\n--- Testing Historical Aggregation (Symbols: {symbols}) ---")
    try:
        articles = await processor.fetch_historical_news(symbols, hours_back)
        print(f"\n[SUCCESS] Aggregated {len(articles)} unique articles across all tiers.")
        
        # Group by source
        stats = {}
        for a in articles:
            stats[a.source] = stats.get(a.source, 0) + 1
            
        for src, count in stats.items():
            print(f"    - {src}: {count} articles")
            
        print("\nTop 5 Articles:")
        for i, a in enumerate(articles[:5]):
            print(f"    {i+1}. [{a.source}] {a.title}")
            
    except Exception as e:
        print(f"\n[ERROR] Historical aggregation failed: {e}")
        import traceback
        traceback.print_exc()

    print("\n--- Testing Deduplication Logic (Mock) ---")
    a1 = Article(
        id="test1",
        symbol="XAU",
        title="Gold prices hit record high amid inflation data",
        content="...",
        source="Source A",
        published_at=datetime.now(),
        url="http://a.com"
    )
    
    a2 = Article(
        id="test2",
        symbol="XAU",
        title="Gold Price hits record high following inflation numbers",
        content="...",
        source="Source B",
        published_at=datetime.now(),
        url="http://b.com"
    )
    
    processor.article_history = [a1]
    processor.processed_article_ids = {a1.id}
    
    is_dup = processor.is_duplicate(a2)
    print(f"Article 1: {a1.title}")
    print(f"Article 2: {a2.title}")
    print(f"Deduplication result (expect True): {is_dup}")
    
    if is_dup:
        print("[SUCCESS] Similarity deduplication detected the match.")
    else:
        print("[FAILURE] Similarity deduplication failed to detect match.")

    print("\nTest Complete.")

if __name__ == "__main__":
    asyncio.run(main())
