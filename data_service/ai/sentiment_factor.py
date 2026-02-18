import logging
import math
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from data_service.ai.sources.base_source import Article
from data_service.storage.database_manager import DatabaseManager
from data_service.utils.config_loader import get_config

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependency
_reliability_tracker = None


def _get_reliability_tracker():
    """Lazy load the reliability tracker."""
    global _reliability_tracker
    if _reliability_tracker is None:
        try:
            from data_service.ai.source_reliability import get_reliability_tracker
            _reliability_tracker = get_reliability_tracker()
        except ImportError:
            _reliability_tracker = None
    return _reliability_tracker


class SentimentFactor:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.config = get_config()
        self.db = db_manager or DatabaseManager()

        # Settings from config or defaults
        proc_settings = self.config.news_sources.get('processing_settings', {})
        self.half_life_hours = proc_settings.get('sentiment_recency_half_life_hours', 2.0)
        self.momentum_window_hours = 6.0
        self.use_dynamic_reliability = proc_settings.get('use_dynamic_reliability', True)

        # In-memory cache for the latest factors
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _get_source_weight(self, source: str) -> float:
        """
        Get source weight using dynamic reliability scoring when available.

        Falls back to static weights if:
        - Dynamic reliability is disabled in config
        - Source doesn't have enough history
        - Reliability tracker unavailable
        """
        # Try dynamic reliability first
        if self.use_dynamic_reliability:
            tracker = _get_reliability_tracker()
            if tracker:
                weight = tracker.get_source_weight(source)
                # Dynamic weights are 0.5-1.0, scale to match old range (0.85-1.2)
                # new_weight = 0.85 + (dynamic_weight - 0.5) * 0.7
                return 0.85 + (weight - 0.5) * 0.7

        # Fallback to static weights
        return self._get_static_source_weight(source)

    @staticmethod
    def _get_static_source_weight(source: str) -> float:
        """Static credibility weight by source (fallback)."""
        s = source.lower()
        if "telegram" in s:
            return 1.2
        if "reuters" in s:
            return 1.05
        if "investing.com" in s:
            return 1.0
        if "cnbc" in s or "yahoo" in s:
            return 0.95
        if "coindesk" in s or "fxstreet" in s:
            return 0.9
        if "duckduckgo" in s:
            return 0.85
        if "google" in s or "rss" in s or "marketwatch" in s:
            return 0.85
        return 0.9  # safe default

    def calculate_decay_weight(self, published_at: datetime) -> float:
        """Calculate exponential recency decay weight."""
        # Handle timezone-aware and naive datetimes
        now = datetime.now()
        if published_at.tzinfo is not None:
            # Convert timezone-aware to naive (assuming UTC for simplicity)
            published_at = published_at.replace(tzinfo=None)
        hours_old = (now - published_at).total_seconds() / 3600.0
        # Decay formula: 2 ^ (-t / T_half)
        return math.pow(2, -max(0, hours_old) / self.half_life_hours)

    def compute_factors(self, symbol: str) -> Dict[str, float]:
        """Compute sentiment factors for a given symbol from recent articles."""
        # 1. Fetch articles from last 24h
        articles = self.db.get_recent_articles(symbol, hours_back=24)
        if not articles:
            return {
                "sentiment_level": 0.0,
                "sentiment_momentum": 0.0,
                "sentiment_variance": 0.0
            }

        # 2. Compute Weighted Mean and Variance
        total_weight = 0.0
        weighted_sum = 0.0
        scores = []
        weights = []

        now = datetime.now()
        for art in articles:
            if art.sentiment_score is None:
                continue
                
            # Base weight from source (substring-matched)
            source_weight = self._get_source_weight(art.source)
            
            # Recency weight
            recency_weight = self.calculate_decay_weight(art.published_at)
            
            combined_weight = source_weight * recency_weight
            
            weighted_sum += art.sentiment_score * combined_weight
            total_weight += combined_weight
            
            scores.append(art.sentiment_score)
            weights.append(combined_weight)

        if total_weight == 0:
            return {"sentiment_level": 0.0, "sentiment_momentum": 0.0, "sentiment_variance": 0.0}

        sentiment_level = weighted_sum / total_weight

        # Variance calculation (weighted)
        weighted_sq_diff_sum = sum(w * (s - sentiment_level)**2 for s, w in zip(scores, weights))
        sentiment_variance = weighted_sq_diff_sum / total_weight

        # 3. Compute Momentum (6h delta)
        # Fetch factors from ~6h ago
        six_h_ago = now - timedelta(hours=self.momentum_window_hours)
        past_factors = self._get_factors_near_timestamp(symbol, six_h_ago)
        
        past_level = past_factors.get('sentiment_level', 0.0) if past_factors else 0.0
        sentiment_momentum = sentiment_level - past_level

        factors = {
            "sentiment_level": round(sentiment_level, 4),
            "sentiment_momentum": round(sentiment_momentum, 4),
            "sentiment_variance": round(sentiment_variance, 4)
        }

        # 4. Persist and Cache
        self.db.save_sentiment_snapshot(symbol, factors, len(articles))
        self._cache[symbol] = factors
        
        return factors

    def _get_factors_near_timestamp(self, symbol: str, ts: datetime) -> Optional[Dict[str, Any]]:
        """Fetch factors from the database closest to a specific timestamp.

        Used for momentum calculation: compares current sentiment level vs
        the level from N hours ago (configured via momentum_window_hours).
        """
        return self.db.get_sentiment_factors_near_timestamp(symbol, ts)

    def get_factors(self, symbol: str) -> Dict[str, float]:
        """Get latest factors for a symbol, computing if not in cache."""
        if symbol not in self._cache:
            return self.compute_factors(symbol)
        return self._cache[symbol]

    def get_cached_factor(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Directly access cache or last DB snapshot."""
        if symbol in self._cache:
            return self._cache[symbol]
        return self.db.get_latest_sentiment_factors(symbol)

    def ingest(self, articles: List[Article]):
        """Ingest articles, save them to DB, and recompute factors."""
        symbols = set()
        for art in articles:
            self.db.save_article(art)
            symbols.add(art.symbol)
        
        for sym in symbols:
            self.compute_factors(sym)
