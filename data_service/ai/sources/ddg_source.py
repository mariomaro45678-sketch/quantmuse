"""
DuckDuckGo News source with production-grade resilience.

Features:
    - Retry logic with exponential backoff
    - Configurable timeouts (per-query and total)
    - Circuit breaker pattern (pauses queries after repeated failures)
    - Graceful degradation (partial results on partial failures)
    - Detailed failure logging for debugging

The library is synchronous, so each call is offloaded to an executor thread.
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import List, Optional

from data_service.ai.sources.base_source import Article, BaseNewsSource

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreakerState:
    """Tracks circuit breaker state for a source."""
    failure_count: int = 0
    last_failure_time: Optional[datetime] = None
    is_open: bool = False

    # Configuration
    failure_threshold: int = 5  # Open after this many consecutive failures
    recovery_timeout: int = 300  # Seconds to wait before trying again (5 min)

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            logger.warning(
                "Circuit breaker OPEN after %d failures. "
                "Will retry in %ds",
                self.failure_count, self.recovery_timeout
            )

    def record_success(self):
        if self.failure_count > 0:
            logger.info("Circuit breaker: success after %d failures", self.failure_count)
        self.failure_count = 0
        self.is_open = False
        self.last_failure_time = None

    def should_attempt(self) -> bool:
        if not self.is_open:
            return True
        # Check if recovery timeout has passed
        if self.last_failure_time:
            elapsed = (datetime.now() - self.last_failure_time).total_seconds()
            if elapsed >= self.recovery_timeout:
                logger.info("Circuit breaker: attempting recovery after %.0fs", elapsed)
                return True
        return False

    def reset(self):
        self.failure_count = 0
        self.last_failure_time = None
        self.is_open = False


class DDGNewsSource(BaseNewsSource):
    """Per-symbol news search via DuckDuckGo with production resilience."""

    # Curated queries per symbol
    SYMBOL_QUERIES = {
        "XAU": ["gold price news today", "gold market analysis"],
        "XAG": ["silver price news today", "silver market news"],
        "BTC": ["bitcoin news today", "BTC crypto market news"],
        "ETH": ["ethereum news today", "ETH crypto news"],
        "TSLA": ["Tesla stock news today"],
        "NVDA": ["NVIDIA stock news today"],
        "AMD": ["AMD stock news today"],
        "COIN": ["Coinbase stock news today"],
        "AAPL": ["Apple stock news today"],
        "GOOGL": ["Google stock news today"],
        "MSFT": ["Microsoft stock news today"],
        "AMZN": ["Amazon stock news today"],
        "META": ["Meta stock news today"],
        "CL": ["crude oil price news today", "WTI oil market"],
        "HG": ["copper price news today"],
    }

    def __init__(self, config: dict):
        super().__init__(config)
        self.max_results_per_query: int = config.get("max_results", 12)

        # Resilience configuration
        self.query_timeout: int = config.get("query_timeout", 15)  # seconds per query
        self.total_timeout: int = config.get("total_timeout", 120)  # max time for all queries
        self.max_retries: int = config.get("max_retries", 3)
        self.base_backoff: float = config.get("base_backoff", 2.0)  # seconds
        self.max_backoff: float = config.get("max_backoff", 30.0)  # seconds

        # Circuit breaker
        self._circuit_breaker = CircuitBreakerState(
            failure_threshold=config.get("circuit_failure_threshold", 5),
            recovery_timeout=config.get("circuit_recovery_timeout", 300),
        )
        self._lock = Lock()

        # Statistics
        self._stats = {
            "queries_attempted": 0,
            "queries_succeeded": 0,
            "queries_failed": 0,
            "total_articles": 0,
            "retries_used": 0,
        }

    def get_source_name(self) -> str:
        return "DuckDuckGo News"

    def get_stats(self) -> dict:
        """Return current statistics for monitoring."""
        with self._lock:
            return {
                **self._stats,
                "circuit_breaker_open": self._circuit_breaker.is_open,
                "consecutive_failures": self._circuit_breaker.failure_count,
            }

    def reset_circuit_breaker(self):
        """Manually reset circuit breaker (useful for testing)."""
        with self._lock:
            self._circuit_breaker.reset()
            logger.info("Circuit breaker manually reset")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_news(self, symbols: List[str], hours_back: int) -> List[Article]:
        # Check circuit breaker
        with self._lock:
            if not self._circuit_breaker.should_attempt():
                logger.warning(
                    "DDG circuit breaker is OPEN - skipping fetch. "
                    "Will retry after recovery timeout."
                )
                return []

        loop = asyncio.get_event_loop()
        try:
            # Run with overall timeout
            articles = await asyncio.wait_for(
                loop.run_in_executor(None, self._fetch_sync, symbols),
                timeout=self.total_timeout
            )

            # Record success if we got any articles
            if articles:
                with self._lock:
                    self._circuit_breaker.record_success()

            return articles

        except asyncio.TimeoutError:
            logger.error("DDG fetch timed out after %ds", self.total_timeout)
            with self._lock:
                self._circuit_breaker.record_failure()
            return []
        except Exception as e:
            logger.error("DDG fetch failed: %s", e)
            with self._lock:
                self._circuit_breaker.record_failure()
            return []

    async def start_stream(self, callback) -> None:
        pass  # polling only

    async def stop_stream(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Sync worker with retry logic (runs in thread-pool executor)
    # ------------------------------------------------------------------

    def _fetch_sync(self, symbols: List[str]) -> List[Article]:
        try:
            from ddgs import DDGS
        except ImportError:
            logger.warning("ddgs not installed. Run: pip install ddgs")
            return []

        articles: List[Article] = []
        seen_urls: set = set()
        queries_done: set = set()
        start_time = time.time()

        # Track per-symbol success for better logging
        symbol_results = {sym: 0 for sym in symbols}

        for symbol in symbols:
            # Check if we've exceeded total timeout
            if time.time() - start_time > self.total_timeout - 10:
                logger.warning("Approaching total timeout, stopping early")
                break

            queries = self.SYMBOL_QUERIES.get(symbol, [f"{symbol} financial news"])

            for query in queries:
                if query in queries_done:
                    continue
                queries_done.add(query)

                with self._lock:
                    self._stats["queries_attempted"] += 1

                query_articles = self._fetch_with_retry(query, symbol, seen_urls)

                if query_articles:
                    articles.extend(query_articles)
                    symbol_results[symbol] += len(query_articles)
                    with self._lock:
                        self._stats["queries_succeeded"] += 1
                        self._stats["total_articles"] += len(query_articles)
                else:
                    with self._lock:
                        self._stats["queries_failed"] += 1

        # Log summary
        successful_symbols = [s for s, c in symbol_results.items() if c > 0]
        failed_symbols = [s for s, c in symbol_results.items() if c == 0]

        if failed_symbols:
            logger.warning(
                "DDG partial results: %d articles from %d/%d symbols. "
                "Failed: %s",
                len(articles), len(successful_symbols), len(symbols),
                ", ".join(failed_symbols[:5])
            )
        else:
            logger.info("DDG success: %d articles from %d symbols",
                       len(articles), len(symbols))

        return articles

    def _fetch_with_retry(
        self,
        query: str,
        symbol: str,
        seen_urls: set
    ) -> List[Article]:
        """Fetch a single query with retry logic and exponential backoff."""
        from ddgs import DDGS

        last_error = None

        for attempt in range(self.max_retries):
            try:
                # Create fresh DDGS instance for each attempt
                with DDGS(timeout=self.query_timeout) as ddgs:
                    results = ddgs.news(
                        query,
                        max_results=self.max_results_per_query,
                        timelimit="d",  # last 24h
                    )

                # Success! Process results
                articles = []
                for r in (results or []):
                    url = r.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    articles.append(self._to_article(r, symbol))

                if attempt > 0:
                    logger.info("Query '%s' succeeded on attempt %d", query, attempt + 1)
                    with self._lock:
                        self._stats["retries_used"] += attempt

                return articles

            except Exception as exc:
                last_error = exc

                # Check if it's a timeout-related error
                is_timeout = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()

                if attempt < self.max_retries - 1:
                    # Calculate backoff with jitter
                    backoff = min(
                        self.base_backoff * (2 ** attempt),
                        self.max_backoff
                    )
                    # Add small random jitter (0-10%)
                    import random
                    backoff *= (1 + random.random() * 0.1)

                    if is_timeout:
                        logger.debug(
                            "Query '%s' timeout (attempt %d/%d), retrying in %.1fs",
                            query, attempt + 1, self.max_retries, backoff
                        )
                    else:
                        logger.debug(
                            "Query '%s' failed (attempt %d/%d): %s, retrying in %.1fs",
                            query, attempt + 1, self.max_retries, exc, backoff
                        )

                    time.sleep(backoff)
                else:
                    # Final attempt failed
                    if is_timeout:
                        logger.warning(
                            "Query '%s' failed after %d attempts (timeout)",
                            query, self.max_retries
                        )
                    else:
                        logger.warning(
                            "Query '%s' failed after %d attempts: %s",
                            query, self.max_retries, exc
                        )

        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_article(result: dict, symbol: str) -> Article:
        title = result.get("title", "")
        url = result.get("url", "")
        body = result.get("body", "")
        date_str = result.get("date", "")

        published_at = datetime.now()
        if date_str:
            try:
                from dateutil import parser as _dp
                published_at = _dp.parse(date_str).replace(tzinfo=None)
            except Exception:
                pass

        aid = hashlib.md5((title + url).encode()).hexdigest()

        return Article(
            id=aid,
            symbol=symbol,
            title=title,
            content=body if body else title,
            source="DuckDuckGo News",
            published_at=published_at,
            url=url,
        )
