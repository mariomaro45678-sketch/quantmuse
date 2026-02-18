import asyncio
import argparse
import logging
import sys
from datetime import datetime
from typing import List

from data_service.ai.news_processor import NewsProcessor
from data_service.ai.nlp_processor import NlpProcessor
from data_service.ai.sentiment_factor import SentimentFactor
from data_service.utils.config_loader import get_config

async def run_demo(symbols: List[str], mode: str):
    print(f"--- Phase 4 Sentiment Analysis Demo (Mode: {mode}) ---")
    
    # 1. Initialize Components
    processor = NewsProcessor(mode=mode)
    nlp = NlpProcessor()
    sf = SentimentFactor()
    
    # 2. Fetch News (Historical)
    print(f"\n[1/3] Fetching news for: {', '.join(symbols)}...")
    articles = await processor.fetch_historical_news(symbols, hours_back=24)
    print(f"Total articles found: {len(articles)}")
    
    if not articles:
        print("No articles found to process.")
        return

    # 3. Analyze NLP
    print(f"\n[2/3] Running NLP pipeline (Sentiment, Keywords, Entities)...")
    scored_articles = []
    for art in articles:
        scored = nlp.analyze(art)
        scored_articles.append(scored)
        print(f"  - [{scored.source}] {scored.title[:60]}... Score: {scored.sentiment_score:.2f}")

    # 4. Compute Factors
    print(f"\n[3/3] Aggregating Sentiment Factors...")
    sf.ingest(scored_articles)
    
    for symbol in symbols:
        factors = sf.get_factors(symbol)
        print(f"\n{symbol} Sentiment Summary:")
        print(f"  Level:    {factors['sentiment_level']:>8.4f}")
        print(f"  Momentum: {factors['sentiment_momentum']:>8.4f}")
        print(f"  Variance: {factors['sentiment_variance']:>8.4f}")
    
    print("\n--- Demo Complete ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sentiment Analysis Demo")
    parser.add_argument("--symbols", type=str, default="XAU,XAG,TSLA", help="Comma-separated symbols")
    parser.add_argument("--mode", type=str, default="mock", help="mode: mock | live")
    args = parser.parse_args()
    
    symbols = [s.strip() for s in args.symbols.split(",")]
    
    # Enable info logging for the demo
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout)
    
    asyncio.run(run_demo(symbols, args.mode))
