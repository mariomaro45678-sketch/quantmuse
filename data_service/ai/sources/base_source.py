from abc import ABC, abstractmethod
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Article:
    id: str  # Unique ID (hash of title + source + time)
    symbol: str
    title: str
    content: str
    source: str
    published_at: datetime
    sentiment_score: Optional[float] = None
    url: Optional[str] = None
    raw_data: Optional[dict] = None

class BaseNewsSource(ABC):
    def __init__(self, config: dict):
        self.config = config
        self.rate_limit = config.get('rate_limit_per_minute', 60)
        self.credibility_weight = config.get('credibility_weight', 0.8)
    
    @abstractmethod
    async def fetch_news(self, symbols: List[str], hours_back: int) -> List[Article]:
        """Fetch news for given symbols. Must implement rate limiting."""
        pass
    
    @abstractmethod
    def get_source_name(self) -> str:
        """Return the source identifier."""
        pass

    @abstractmethod
    async def start_stream(self, callback) -> None:
        """Start real-time stream if supported. Callback receives Article."""
        pass
    
    @abstractmethod
    async def stop_stream(self) -> None:
        """Stop real-time stream."""
        pass
