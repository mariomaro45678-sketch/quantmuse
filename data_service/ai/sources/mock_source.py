import asyncio
import logging
from datetime import datetime, timedelta
from typing import List
from data_service.ai.sources.base_source import BaseNewsSource, Article

logger = logging.getLogger(__name__)

class MockNewsSource(BaseNewsSource):
    """
    Minimalist mock source for unit testing the NLP pipeline.
    Returns deterministic 'news' articles.
    """
    def __init__(self, config: dict):
        super().__init__(config)
        self.interval = config.get('interval_seconds', 10)

    def get_source_name(self) -> str:
        return "Mock News Source"

    async def fetch_news(self, symbols: List[str], hours_back: int) -> List[Article]:
        """Return a static list of mock articles, filtered by symbol."""
        all_articles = [
            Article(
                id="mock_gold_1",
                symbol="XAU",
                title="Gold surges as inflation fears grip markets",
                content="Gold prices touched new highs today as investors sought safety amid rising CPI data.",
                source="Mock Source",
                published_at=datetime.now() - timedelta(minutes=5),
                url="http://mock.com/gold/1"
            ),
            Article(
                id="mock_tsla_1",
                symbol="TSLA",
                title="Tesla deliveries beat expectations in Q4",
                content="Elon Musk announced record-breaking delivery numbers for the final quarter of the year.",
                source="Mock Source",
                published_at=datetime.now() - timedelta(minutes=15),
                url="http://mock.com/tsla/1"
            )
        ]
        if not symbols:
            return all_articles
        return [a for a in all_articles if a.symbol in symbols]

    async def start_stream(self, callback) -> None:
        """Periodically trigger the callback with mock articles."""
        while True:
            articles = await self.fetch_news([], 1)
            for a in articles:
                if callback:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(a)
                    else:
                        callback(a)
            await asyncio.sleep(self.interval)

    async def stop_stream(self) -> None:
        pass
