"""
Multi-feed RSS source.  Fetches 8+ static financial RSS feeds concurrently
via aiohttp, parses with feedparser, filters for relevance, and assigns
symbols via keyword matching.

All feeds are fetched in a single asyncio.gather() call so total latency
is dominated by the *slowest* feed, not the sum of all feeds.
"""

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta
from time import mktime
from typing import Dict, List

import aiohttp
import feedparser

from data_service.ai.sources.base_source import Article, BaseNewsSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Symbol assignment – ordered by specificity so "gold price" beats "gold"
# ---------------------------------------------------------------------------
_SYMBOL_RULES: List[tuple] = [
    ("gold price", "XAU"), ("xau", "XAU"), ("gold", "XAU"),
    ("silver price", "XAG"), ("xag", "XAG"), ("silver", "XAG"),
    ("bitcoin", "BTC"), ("btc", "BTC"),
    ("ethereum", "ETH"), ("ether", "ETH"),
    ("tesla", "TSLA"), ("tsla", "TSLA"),
    ("nvidia", "NVDA"), ("nvda", "NVDA"),
    ("crude oil", "CL"), ("wti", "CL"), ("oil price", "CL"),
    ("copper", "HG"),
]

# At least one of these must appear for the article to pass the relevance gate
_RELEVANCE_KW = frozenset({
    "gold", "xau", "silver", "xag", "bitcoin", "btc", "ethereum", "eth",
    "tesla", "tsla", "nvidia", "nvda", "oil", "crude", "copper",
    "fed", "inflation", "cpi", "stock", "market", "bullish", "bearish",
    "forex", "trading", "commodity", "commodities", "crypto", "nasdaq",
})

# ---------------------------------------------------------------------------
# Default feed catalogue – all free, no auth required
# ---------------------------------------------------------------------------
DEFAULT_FEEDS: Dict[str, str] = {
    "reuters_top":       "https://feeds.reuters.com/reuters/topNews",
    "reuters_biz":       "https://feeds.reuters.com/reuters/businessNews",
    "cnbc":              "https://www.cnbc.com/feeds/rss/top_news/index.xml",
    "yahoo_finance":     "https://finance.yahoo.com/rss/",
    "marketwatch":       "https://www.marketwatch.com/feeds/rss/10best/subheadline/stocks/news",
    "coindesk":          "https://www.coindesk.com/arc/outbrain/?rssId=all",
    "fxstreet_news":     "https://www.fxstreet.com/feeds/rss/News_en.xml",
    "fxstreet_analysis": "https://www.fxstreet.com/feeds/rss/Analysis_en.xml",
}

_FETCH_TIMEOUT_SECS = 12
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
)


class RSSMultiSource(BaseNewsSource):
    """Fetches financial news from multiple RSS feeds concurrently."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.feeds: Dict[str, str] = config.get("feeds", DEFAULT_FEEDS)

    def get_source_name(self) -> str:
        return "RSS Multi"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_news(self, symbols: List[str], hours_back: int) -> List[Article]:
        timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECS)
        headers = {"User-Agent": _USER_AGENT}

        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            tasks = [
                self._fetch_one(session, name, url, hours_back)
                for name, url in self.feeds.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        articles: List[Article] = []
        for name, res in zip(self.feeds.keys(), results):
            if isinstance(res, Exception):
                logger.debug("Feed %s error: %s", name, res)
            else:
                articles.extend(res)

        return [a for a in articles if self._is_relevant(a)]

    async def start_stream(self, callback) -> None:
        pass  # polling handled by the collector loop

    async def stop_stream(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_one(
        self,
        session: aiohttp.ClientSession,
        feed_name: str,
        url: str,
        hours_back: int,
    ) -> List[Article]:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()
            feed = feedparser.parse(text)
            return self._parse_entries(feed, feed_name, hours_back)
        except asyncio.TimeoutError:
            logger.debug("Feed %s timed out", feed_name)
            return []
        except Exception as exc:
            logger.debug("Feed %s: %s", feed_name, exc)
            return []

    def _parse_entries(self, feed, feed_name: str, hours_back: int) -> List[Article]:
        articles: List[Article] = []
        cutoff = datetime.now() - timedelta(hours=hours_back)

        for entry in feed.entries:
            try:
                # --- timestamp ---
                parsed_time = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
                published_at = datetime.fromtimestamp(mktime(parsed_time)) if parsed_time else datetime.now()
                if published_at < cutoff:
                    continue

                # --- title ---
                title = (entry.get("title") or "").strip()
                if not title:
                    continue

                # --- link ---
                link = entry.get("link", "")

                # --- body / summary (strip HTML tags) ---
                summary = entry.get("summary") or entry.get("description") or ""
                summary = re.sub(r"<[^>]+>", " ", summary).strip()
                content = summary if summary else title

                # --- source label ---
                src = entry.get("source", None)
                source_tag = (src.title if hasattr(src, "title") else feed_name) if src else feed_name

                # --- symbol & ID ---
                symbol = self._assign_symbol(title + " " + content)
                aid = hashlib.md5((title + link).encode()).hexdigest()

                articles.append(Article(
                    id=aid,
                    symbol=symbol,
                    title=title,
                    content=content,
                    source=f"RSS ({source_tag})",
                    published_at=published_at,
                    url=link,
                ))
            except Exception:
                continue

        return articles

    # ------------------------------------------------------------------
    # Symbol / relevance
    # ------------------------------------------------------------------

    @staticmethod
    def _assign_symbol(text: str) -> str:
        lower = text.lower()
        for kw, sym in _SYMBOL_RULES:
            if kw in lower:
                return sym
        return "GENERAL"

    @staticmethod
    def _is_relevant(article: Article) -> bool:
        text = (article.title + " " + (article.content or "")).lower()
        return any(kw in text for kw in _RELEVANCE_KW)
