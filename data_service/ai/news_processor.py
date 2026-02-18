import logging
import asyncio
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timedelta
import os
import re
import time
from urllib.parse import urlparse, urlencode, parse_qs

from data_service.ai.sources.base_source import Article, BaseNewsSource
from data_service.ai.sources.telegram_source import TelegramSource
from data_service.ai.sources.investing_com_source import InvestingComSource
from data_service.ai.sources.google_rss_source import GoogleRSSSource
from data_service.ai.sources.rss_multi_source import RSSMultiSource
from data_service.ai.sources.ddg_source import DDGNewsSource
from data_service.utils.config_loader import get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fast dedup helpers (module-level, shared by NewsProcessor and NewsCollector)
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to",
    "for", "of", "and", "or", "but", "as", "with", "this", "that", "it",
    "its", "be", "has", "have", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "not", "no", "so", "if",
    "than", "too", "very", "just", "also", "now", "new", "from", "by", "up",
})

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content",
    "utm_term", "ref", "mbid", "fbclid", "gclid",
})


def normalize_url(url: str) -> str:
    """Strip tracking params & fragment so the same article from different
    referrers maps to the same key."""
    if not url:
        return ""
    try:
        p = urlparse(url)
        clean = {k: v for k, v in parse_qs(p.query).items()
                 if k.lower() not in _TRACKING_PARAMS}
        q = urlencode(clean, doseq=True)
        return f"{p.scheme}://{p.netloc}{p.path}{'?' + q if q else ''}"
    except Exception:
        return url


def extract_title_words(title: str) -> frozenset:
    """Bag-of-words fingerprint: lowercase, no punctuation, no stopwords,
    min 3 chars.  Used for Jaccard similarity."""
    words = re.sub(r"[^a-z0-9\s]", "", title.lower()).split()
    return frozenset(w for w in words if w not in _STOPWORDS and len(w) > 2)


def jaccard_similarity(set_a: frozenset, set_b: frozenset) -> float:
    """Jaccard index.  O(min(|A|,|B|)) thanks to Python set internals."""
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)

class NewsProcessor:
    def __init__(self, mode: str = 'live'):
        self.mode = mode
        self.config = get_config()
        self.news_config = self.config.news_sources
        
        self.sources: Dict[str, BaseNewsSource] = {}

        # --- 3-tier dedup state ---
        self.processed_article_ids: set = set()                          # Tier 1 – exact ID
        self.seen_urls: set = set()                                      # Tier 2 – canonical URL
        self.title_fingerprints: List[tuple] = []                        # Tier 3 – (frozenset, datetime)
        self.similarity_threshold = 0.65                                 # Jaccard threshold
        self.history_limit_hours = 6
        self.url_dedup_ttl_hours = 24
        self.article_history: List[Article] = []                         # kept for legacy callbacks
        
        # Performance Tracking
        self.stats = {
            # "source_name": {"latency_sums": 0, "calls": 0, "errors": 0}
        }
        
        self.on_article_received: Optional[Callable] = None
        self._init_sources()

    def _init_sources(self):
        """Register sources based on config."""
        if self.mode == 'mock':
            from data_service.ai.sources.mock_source import MockNewsSource
            self.sources['mock'] = MockNewsSource({})
            logger.info("Initialized in MOCK mode with MockNewsSource")
            return

        sources_config = self.news_config.get('sources', {})
        
        # Telegram
        tg_conf = sources_config.get('telegram', {})
        if tg_conf.get('enabled'):
            api_id = os.getenv('TELEGRAM_API_ID')
            api_hash = os.getenv('TELEGRAM_API_HASH')
            phone = os.getenv('TELEGRAM_PHONE')
            
            if api_id and api_hash:
                self.sources['telegram'] = TelegramSource({
                    **tg_conf,
                    'api_id': api_id,
                    'api_hash': api_hash,
                    'phone': phone
                })
        
        # Scraping
        scrap_conf = sources_config.get('scraping', {}).get('investing_com', {})
        if scrap_conf.get('enabled'):
            self.sources['investing.com'] = InvestingComSource(scrap_conf)
            
        # Google RSS
        rss_conf = sources_config.get('rss', {}).get('google', {})
        if rss_conf.get('enabled'):
            self.sources['google_rss'] = GoogleRSSSource(rss_conf)

        # RSS Multi (concurrent static feeds)
        rss_multi_conf = sources_config.get('rss', {}).get('multi', {})
        if rss_multi_conf.get('enabled', True):
            self.sources['rss_multi'] = RSSMultiSource(rss_multi_conf)

        # DuckDuckGo News
        ddg_conf = sources_config.get('ddg', {})
        if ddg_conf.get('enabled', True):
            self.sources['ddg'] = DDGNewsSource(ddg_conf)

    async def start(self, callback: Callable):
        """Start all enabled sources."""
        self.on_article_received = callback
        tasks = []
        
        for name, source in self.sources.items():
            logger.info(f"Starting news source: {name}")
            tasks.append(source.start_stream(self._handle_new_article))
            
        await asyncio.gather(*tasks)

    async def fetch_historical_news(self, symbols: List[str], hours_back: int) -> List[Article]:
        """Manual fetch from all enabled sources."""
        all_articles = []
        for name, source in self.sources.items():
            try:
                start_time = time.time()
                articles = await source.fetch_news(symbols, hours_back)
                latency = (time.time() - start_time) * 1000
                
                self._record_stat(name, latency)
                all_articles.extend(articles)
            except Exception as e:
                logger.error(f"Error fetching from {name}: {e}")
                self._record_stat(name, 0, error=True)
                
        return self.deduplicate(self.filter_by_relevance(all_articles))

    def _record_stat(self, source: str, latency: float, error: bool = False):
        if source not in self.stats:
            self.stats[source] = {"latency_sum": 0, "calls": 0, "errors": 0}
        
        if not error:
            self.stats[source]["latency_sum"] += latency
            self.stats[source]["calls"] += 1
        else:
            self.stats[source]["errors"] += 1

    def get_source_stats(self) -> Dict[str, Any]:
        """Returns avg latency and error counts per source."""
        report = {}
        for src, data in self.stats.items():
            avg = data["latency_sum"] / data["calls"] if data["calls"] > 0 else 0
            report[src] = {
                "avg_latency_ms": round(avg, 2),
                "calls": data["calls"],
                "errors": data["errors"]
            }
        return report

    async def _handle_new_article(self, article: Article):
        """Callback for sources."""
        if self.is_duplicate(article):
            return

        # Mark seen across all 3 tiers
        self.processed_article_ids.add(article.id)
        norm = normalize_url(article.url)
        if norm:
            self.seen_urls.add(norm)
        self.title_fingerprints.append((extract_title_words(article.title), article.published_at))
        self.article_history.append(article)
        self._cleanup_history()

        if self.on_article_received:
            if asyncio.iscoroutinefunction(self.on_article_received):
                await self.on_article_received(article)
            else:
                self.on_article_received(article)

    def is_duplicate(self, article: Article) -> bool:
        """3-tier duplicate check: ID → URL → title Jaccard.
        Each tier is fast-fail; we never touch SequenceMatcher."""
        # Tier 1 – exact ID
        if article.id in self.processed_article_ids:
            return True
        # Tier 2 – canonical URL
        norm = normalize_url(article.url)
        if norm and norm in self.seen_urls:
            return True
        # Tier 3 – Jaccard on word-set fingerprint
        words = extract_title_words(article.title)
        for fp, _ in self.title_fingerprints:
            if jaccard_similarity(words, fp) >= self.similarity_threshold:
                logger.debug("Duplicate (Jaccard): %s", article.title[:60])
                return True
        return False

    def deduplicate(self, articles: List[Article]) -> List[Article]:
        """Batch dedup: checks each article against persistent state AND
        against previously accepted articles in the same batch."""
        unique: List[Article] = []
        batch_ids: set = set()
        batch_urls: set = set()
        batch_fps: List[frozenset] = []

        for a in articles:
            # --- ID ---
            if a.id in self.processed_article_ids or a.id in batch_ids:
                continue
            # --- URL ---
            norm = normalize_url(a.url)
            if norm and (norm in self.seen_urls or norm in batch_urls):
                continue
            # --- Jaccard vs history ---
            words = extract_title_words(a.title)
            dup = False
            for fp, _ in self.title_fingerprints:
                if jaccard_similarity(words, fp) >= self.similarity_threshold:
                    dup = True
                    break
            # --- Jaccard vs batch ---
            if not dup:
                for fp in batch_fps:
                    if jaccard_similarity(words, fp) >= self.similarity_threshold:
                        dup = True
                        break
            if dup:
                continue

            unique.append(a)
            batch_ids.add(a.id)
            if norm:
                batch_urls.add(norm)
            batch_fps.append(words)

        return unique

    def filter_by_relevance(self, articles: List[Article]) -> List[Article]:
        """Basic keyword-based filtering to remove non-financial noise."""
        finance_keywords = ["gold", "xau", "silver", "xag", "oil", "fed", "inflation", "cpi", "tsla", "nvda", "bullish", "bearish", "crypto", "stock"]
        filtered = []
        for art in articles:
            text = (art.title + " " + art.content).lower()
            if any(kw in text for kw in finance_keywords):
                filtered.append(art)
        return filtered

    def _cleanup_history(self):
        """Prune all dedup structures to their configured TTLs."""
        now = datetime.now()
        history_cutoff = now - timedelta(hours=self.history_limit_hours)
        self.article_history = [a for a in self.article_history if a.published_at > history_cutoff]
        self.title_fingerprints = [(fp, ts) for fp, ts in self.title_fingerprints if ts > history_cutoff]

        # Hard-cap sets to avoid unbounded growth in long-running processes
        if len(self.processed_article_ids) > 2000:
            self.processed_article_ids = {a.id for a in self.article_history}
        if len(self.seen_urls) > 2000:
            self.seen_urls = {normalize_url(a.url) for a in self.article_history if a.url}
