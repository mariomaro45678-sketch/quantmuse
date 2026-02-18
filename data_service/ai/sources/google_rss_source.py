import logging
import urllib.parse
from datetime import datetime
from time import mktime
from typing import List, Optional
import feedparser
import hashlib
from data_service.ai.sources.base_source import BaseNewsSource, Article

logger = logging.getLogger(__name__)

class GoogleRSSSource(BaseNewsSource):
    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = "https://news.google.com/rss/search?q="
        self.fetch_interval_minutes = config.get('fetch_interval_minutes', 5)

    def get_source_name(self) -> str:
        return "Google News RSS"

    async def fetch_news(self, symbols: List[str], hours_back: int) -> List[Article]:
        """
        Fetch news from Google RSS for given symbols.
        Constructs a query like: "XAU OR GOLD OR SILVER when:6h"
        """
        articles = []
        
        # Group symbols to reduce requests, but Google RSS URL length is limited.
        # Simple strategy: 1 query for all, or split if too many.
        # For now, let's create a combined query string for the main assets.
        
        # We need to map symbols to search terms usually
        # e.g. XAU -> "Gold price", TSLA -> "Tesla stock"
        # For this MVP, we assume symbols/keywords are passed directly
        
        # Helper to format query
        # q=XAU%20OR%20GOLD%20when%3A6h&hl=en-US&gl=US&ceid=US%3Aen
        
        query_terms = " OR ".join(symbols)
        query = f"{query_terms} when:{hours_back}h"
        encoded_query = urllib.parse.quote(query)
        
        url = f"{self.base_url}{encoded_query}&hl=en-US&gl=US&ceid=US:en"
        
        try:
            feed = feedparser.parse(url)
            
            if feed.bozo:
                logger.warning(f"Error parsing Google RSS feed: {feed.bozo_exception}")
                return []
                
            for entry in feed.entries:
                try:
                    # Extract timestamp
                    published_at = datetime.fromtimestamp(mktime(entry.published_parsed))
                    
                    # Generate ID
                    aid = hashlib.md5((entry.title + entry.link).encode()).hexdigest()
                    
                    articles.append(Article(
                        id=aid,
                        symbol="GENERAL", # Will be refined by NLP/Entity extraction
                        title=entry.title,
                        content=entry.title, # RSS often has no summary or short snippet
                        source=f"Google News ({entry.source.title if 'source' in entry else 'Unknown'})",
                        published_at=published_at,
                        url=entry.link,
                        sentiment_score=None
                    ))
                except Exception as e:
                    continue
                    
        except Exception as e:
            logger.error(f"Error fetching Google RSS: {e}")
            
        return articles

    async def start_stream(self, callback) -> None:
        """RSS is polling-based, not streaming. No-op or simulated poll."""
        pass 

    async def stop_stream(self) -> None:
        pass
