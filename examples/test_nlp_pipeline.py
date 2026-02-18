import asyncio
from datetime import datetime
from data_service.ai.nlp_processor import NlpProcessor
from data_service.ai.sources.base_source import Article

async def run_demo():
    print("Initializing NLP Processor...")
    nlp = NlpProcessor()
    
    mock_articles = [
        Article(
            id="mock_1",
            symbol="XAU",
            title="Gold Prices Surging Amid Inflation Fears",
            content="Analysts are bullish on gold as inflation data shows a sharp rise. The Fed is expected to stay dovish, supporting a rally in precious metals.",
            source="Mock News",
            published_at=datetime.now()
        ),
        Article(
            id="mock_2",
            symbol="TSLA",
            title="Tesla shares drop on supply chain worries",
            content="Tesla (TSLA) stock is under pressure today after reports of manufacturing delays in China. Investors are bearish as competition from BYD increases.",
            source="Mock News",
            published_at=datetime.now()
        )
    ]
    
    print("\n--- Processing Articles ---\n")
    for article in mock_articles:
        print(f"Original Title: {article.title}")
        analyzed = nlp.analyze(article)
        print(f"Sentiment Score: {analyzed.sentiment_score:.3f}")
        print(f"Keywords: {analyzed.raw_data['keywords']}")
        print(f"Entities: {analyzed.raw_data['entities']}")
        print("-" * 30)

if __name__ == "__main__":
    asyncio.run(run_demo())
