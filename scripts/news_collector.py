#!/usr/bin/env python3
"""
Production-grade Multi-source News Collection Service.

Features:
    1. Multiple news sources (Google RSS, RSS Multi, DuckDuckGo)
    2. Robust error handling with graceful degradation
    3. Health monitoring with heartbeat file
    4. Automatic recovery from transient failures
    5. Comprehensive statistics and logging
    6. Clean shutdown handling

Sources (all fetched concurrently each cycle):
    1. Google News RSS   – per-symbol dynamic queries
    2. RSS Multi         – 8 static feeds (Reuters, CNBC, Yahoo…) via aiohttp
    3. DuckDuckGo News   – per-symbol search with retry logic

Deduplication (3-tier, all O(1) or O(k)):
    Tier 1 – exact article ID (hash set)
    Tier 2 – canonical URL    (hash set, tracking params stripped)
    Tier 3 – title word-set Jaccard similarity (threshold 0.65)

Usage:
    python scripts/news_collector.py [--interval 5] [--symbols XAU,XAG,TSLA,NVDA]
"""

import asyncio
import argparse
import json
import logging
import os
import signal
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Set, Tuple, Dict, Any

# ---------------------------------------------------------------------------
# path bootstrap
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.ai.sources.google_rss_source import GoogleRSSSource
from data_service.ai.sources.rss_multi_source import RSSMultiSource
from data_service.ai.sources.ddg_source import DDGNewsSource
from data_service.ai.sources.telegram_source import TelegramSource
from data_service.ai.news_processor import (
    normalize_url, extract_title_words, jaccard_similarity,
)
from data_service.ai.nlp_processor import NlpProcessor
from data_service.ai.sentiment_factor import SentimentFactor
from data_service.storage.database_manager import DatabaseManager
from data_service.utils.logging_config import setup_logging
from data_service.utils.config_loader import get_config

setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HEALTH_FILE = Path(__file__).parent.parent / "logs" / "news_collector_health.json"
MIN_CYCLE_INTERVAL = 60  # Minimum seconds between cycles (safety)
MAX_CONSECUTIVE_FAILURES = 10  # Stop if too many failures in a row

# ---------------------------------------------------------------------------
# Breaking news detection keywords (process immediately, don't wait for batch)
# ---------------------------------------------------------------------------
BREAKING_KEYWORDS = frozenset([
    "breaking", "just in", "alert", "urgent", "flash",
    "fed cuts", "fed hikes", "rate decision", "fomc",
    "earnings beat", "earnings miss", "guidance",
    "crash", "surge", "plunge", "soar", "tank",
    "bankruptcy", "acquisition", "merger",
    "sec charges", "investigation", "lawsuit",
    "cpi", "jobs report", "nonfarm", "gdp",
])


def _is_breaking_news(article) -> bool:
    """Detect if an article is breaking/urgent news that should be processed immediately."""
    text = f"{article.title} {article.content or ''}".lower()
    return any(kw in text for kw in BREAKING_KEYWORDS)


# ---------------------------------------------------------------------------
# Symbol ← keyword mapping (used to fix "GENERAL" articles from RSS)
# ---------------------------------------------------------------------------
_SYMBOL_RULES: List[tuple] = [
    ("gold price", "XAU"), ("xau", "XAU"), ("gold", "XAU"),
    ("silver price", "XAG"), ("xag", "XAG"), ("silver", "XAG"),
    ("bitcoin", "BTC"), ("btc", "BTC"),
    ("ethereum", "ETH"),
    ("tesla", "TSLA"), ("tsla", "TSLA"),
    ("nvidia", "NVDA"), ("nvda", "NVDA"),
    ("amd", "AMD"),
    ("coinbase", "COIN"), ("coin", "COIN"),
    ("apple", "AAPL"), ("aapl", "AAPL"),
    ("google", "GOOGL"), ("alphabet", "GOOGL"), ("googl", "GOOGL"),
    ("microsoft", "MSFT"), ("msft", "MSFT"),
    ("amazon", "AMZN"), ("amzn", "AMZN"),
    ("meta", "META"), ("facebook", "META"),
    ("crude oil", "CL"), ("wti", "CL"),
    ("copper", "HG"),
]


def _assign_symbol(text: str) -> str:
    lower = text.lower()
    for kw, sym in _SYMBOL_RULES:
        if kw in lower:
            return sym
    return "GENERAL"


def _to_naive(dt) -> datetime:
    """Convert any datetime to timezone-naive (UTC) for safe comparisons."""
    if dt is None:
        return datetime.now()
    if not isinstance(dt, datetime):
        return datetime.now()
    if dt.tzinfo is not None:
        # Convert to UTC then strip tzinfo
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# NewsCollector
# ---------------------------------------------------------------------------
class NewsCollector:
    """Production-grade background daemon for news collection."""

    # Search-term expansions fed to Google RSS (per symbol)
    SYMBOL_SEARCH_TERMS = {
        "XAU": ["GOLD", "XAU", "gold price", "gold market"],
        "XAG": ["SILVER", "XAG", "silver price"],
        "BTC": ["BITCOIN", "BTC", "bitcoin price"],
        "ETH": ["ETHEREUM", "ETH", "ethereum price"],
        "TSLA": ["TESLA", "TSLA"],
        "NVDA": ["NVIDIA", "NVDA"],
        "AMD": ["AMD", "Advanced Micro Devices"],
        "COIN": ["COINBASE", "COIN"],
        "AAPL": ["APPLE", "AAPL"],
        "GOOGL": ["GOOGLE", "GOOGL", "ALPHABET"],
        "MSFT": ["MICROSOFT", "MSFT"],
        "AMZN": ["AMAZON", "AMZN"],
        "META": ["META", "FACEBOOK"],
        "CL": ["crude oil", "OIL", "WTI"],
        "HG": ["COPPER", "copper price"],
    }

    DEDUP_JACCARD_THRESHOLD = 0.65

    def __init__(self, symbols: list, interval_minutes: int = 5):
        self.symbols = symbols
        self.interval_minutes = max(1, interval_minutes)  # At least 1 minute
        self.running = False
        self._shutdown_event = asyncio.Event()

        # --- NLP / DB ---
        self.db = DatabaseManager()
        self.nlp = NlpProcessor()
        self.sentiment_factor = SentimentFactor(db_manager=self.db)

        # --- sources with resilience config ---
        self.google_rss = GoogleRSSSource({})
        self.rss_multi = RSSMultiSource({})
        self.ddg = DDGNewsSource({
            "query_timeout": 15,
            "total_timeout": 120,
            "max_retries": 3,
            "circuit_failure_threshold": 5,
            "circuit_recovery_timeout": 300,
        })

        # --- Telegram source (real-time, lowest latency) ---
        self.telegram = None
        telegram_config = self._load_telegram_config()
        if telegram_config:
            self.telegram = TelegramSource(telegram_config)
            logger.info("Telegram source initialized with %d channels", len(telegram_config.get('channels', [])))

        # --- statistics ---
        self.stats = {
            "start_time": None,
            "cycles": 0,
            "total_fetched": 0,
            "unique_processed": 0,
            "last_cycle_time": None,
            "last_cycle_articles": 0,
            "consecutive_failures": 0,
            "source_stats": {
                "google_rss": {"fetched": 0, "failures": 0},
                "rss_multi": {"fetched": 0, "failures": 0},
                "ddg": {"fetched": 0, "failures": 0},
                "telegram": {"fetched": 0, "failures": 0},
            },
        }

        # --- dedup state ---
        self.seen_ids: Set[str] = set()
        self.seen_urls: Set[str] = set()
        self.title_fps: List[Tuple[frozenset, datetime]] = []

        # seed from DB so a restart doesn't re-process yesterday's articles
        self._seed_dedup_from_db()

    def _load_telegram_config(self) -> dict:
        """Load Telegram configuration from config file and environment."""
        try:
            config = get_config()
            tg_config = config.news_sources.get('sources', {}).get('telegram', {})

            if not tg_config.get('enabled', False):
                logger.info("Telegram source is disabled in config")
                return {}

            # Load credentials from environment variables
            api_id = os.environ.get(tg_config.get('api_id_env', 'TELEGRAM_API_ID'))
            api_hash = os.environ.get(tg_config.get('api_hash_env', 'TELEGRAM_API_HASH'))
            phone = os.environ.get(tg_config.get('phone_env', 'TELEGRAM_PHONE'))

            if not all([api_id, api_hash, phone]):
                logger.warning("Telegram credentials not fully configured (missing env vars)")
                return {}

            return {
                'api_id': int(api_id),
                'api_hash': api_hash,
                'phone': phone,
                'channels': tg_config.get('channels', []),
                'keywords': tg_config.get('keywords', self.symbols),
            }
        except Exception as e:
            logger.error("Failed to load Telegram config: %s", e)
            return {}

    # ------------------------------------------------------------------
    # Dedup – seed & check
    # ------------------------------------------------------------------

    def _seed_dedup_from_db(self):
        """Load IDs / URLs / fingerprints for articles already in the DB.
        Reduced to 12h to save memory while still preventing duplicates."""
        logger.info("Seeding dedup state from database...")
        count = 0
        for sym in self.symbols:
            try:
                for art in self.db.get_recent_articles(sym, hours_back=12):
                    self.seen_ids.add(art.id)
                    if art.url:
                        self.seen_urls.add(normalize_url(art.url))
                    if art.title:
                        ts = _to_naive(art.published_at)
                        self.title_fps.append((extract_title_words(art.title), ts))
                    count += 1
            except Exception as exc:
                logger.debug("Dedup seed for %s: %s", sym, exc)
        logger.info("Seeded %d articles from database", count)

    def _is_duplicate(self, article) -> bool:
        # Tier 1 - ID
        if article.id in self.seen_ids:
            return True
        # Tier 2 - URL
        nu = normalize_url(article.url)
        if nu and nu in self.seen_urls:
            return True
        # Tier 3 - Title similarity
        tw = extract_title_words(article.title)
        for fp, _ in self.title_fps:
            if jaccard_similarity(tw, fp) >= self.DEDUP_JACCARD_THRESHOLD:
                return True
        return False

    def _mark_seen(self, article):
        self.seen_ids.add(article.id)
        nu = normalize_url(article.url)
        if nu:
            self.seen_urls.add(nu)
        self.title_fps.append((extract_title_words(article.title), datetime.now()))

    def _cleanup_dedup(self):
        """Prune stale entries; hard-reset if sets grow too large.
        Memory optimization: reduced from 4000 to 2000 limit."""
        cutoff = datetime.now() - timedelta(hours=12)  # Reduced from 24h to 12h
        self.title_fps = [(fp, ts) for fp, ts in self.title_fps if ts > cutoff]
        # Reduced threshold from 4000 to 2000 to save ~50% memory
        if len(self.seen_ids) > 2000:
            logger.info("Dedup cache reset (exceeded 2000 entries)")
            self.seen_ids.clear()
            self.seen_urls.clear()
            self.title_fps.clear()
            self._seed_dedup_from_db()

    # ------------------------------------------------------------------
    # Health monitoring
    # ------------------------------------------------------------------

    def _write_health(self, status: str = "healthy", error: str = None):
        """Write health status to file for external monitoring."""
        try:
            health_data = {
                "status": status,
                "timestamp": datetime.now().isoformat(),
                "uptime_seconds": (datetime.now() - self.stats["start_time"]).total_seconds() if self.stats["start_time"] else 0,
                "cycles": self.stats["cycles"],
                "last_cycle": self.stats["last_cycle_time"].isoformat() if self.stats["last_cycle_time"] else None,
                "articles_processed": self.stats["unique_processed"],
                "consecutive_failures": self.stats["consecutive_failures"],
                "error": error,
                "source_stats": self.stats["source_stats"],
            }
            HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
            HEALTH_FILE.write_text(json.dumps(health_data, indent=2))
        except Exception as e:
            logger.debug("Failed to write health file: %s", e)

    # ------------------------------------------------------------------
    # Fetch – all sources in parallel with error isolation
    # ------------------------------------------------------------------

    async def _fetch_all(self) -> Tuple[List, Dict[str, Any]]:
        """Fetch from all sources with graceful degradation."""
        # Flatten search terms for Google RSS combined query
        all_terms: List[str] = []
        for sym in self.symbols:
            all_terms.extend(self.SYMBOL_SEARCH_TERMS.get(sym, [sym]))

        # Build list of fetch tasks (Telegram is optional)
        fetch_tasks = [
            self._fetch_with_timeout("google_rss", self.google_rss.fetch_news(all_terms, hours_back=6)),
            self._fetch_with_timeout("rss_multi", self.rss_multi.fetch_news(self.symbols, hours_back=6)),
            self._fetch_with_timeout("ddg", self.ddg.fetch_news(self.symbols, hours_back=6)),
        ]
        labels = ["google_rss", "rss_multi", "ddg"]

        # Add Telegram if configured (lowest latency source)
        if self.telegram:
            fetch_tasks.append(
                self._fetch_with_timeout("telegram", self.telegram.fetch_news(self.symbols, hours_back=1), timeout=30)
            )
            labels.append("telegram")

        # Fetch all sources in parallel with timeout protection
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        all_articles = []
        source_results = {}

        for label, res in zip(labels, results):
            if isinstance(res, Exception):
                logger.error("[%s] fetch error: %s", label.upper(), res)
                self.stats["source_stats"][label]["failures"] += 1
                source_results[label] = {"status": "error", "count": 0, "error": str(res)}
            elif isinstance(res, list):
                count = len(res)
                logger.info("[%s] fetched %d articles", label.upper(), count)
                self.stats["source_stats"][label]["fetched"] += count
                all_articles.extend(res)
                source_results[label] = {"status": "ok", "count": count}
            else:
                # Unexpected result type
                logger.warning("[%s] unexpected result type: %s", label, type(res))
                source_results[label] = {"status": "unknown", "count": 0}

        return all_articles, source_results

    async def _fetch_with_timeout(self, name: str, coro, timeout: int = 60):
        """Wrap a fetch coroutine with a timeout.
        Reduced from 180s to 60s for faster failure detection."""
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.error("[%s] fetch timed out after %ds", name, timeout)
            raise
        except Exception as e:
            logger.error("[%s] fetch failed: %s", name, e)
            raise

    # ------------------------------------------------------------------
    # Main cycle
    # ------------------------------------------------------------------

    async def fetch_and_process(self) -> bool:
        """Run one collection cycle. Returns True on success, False on failure."""
        cycle_start = datetime.now()
        self.stats["cycles"] += 1
        cycle_num = self.stats["cycles"]

        logger.info("=" * 60)
        logger.info("CYCLE %d: Starting fetch from 3 sources", cycle_num)
        logger.info("=" * 60)

        try:
            # 1. Parallel fetch with error isolation
            all_articles, source_results = await self._fetch_all()
            self.stats["total_fetched"] += len(all_articles)

            # Check if we got ANY articles
            if not all_articles:
                # All sources failed
                logger.warning("Cycle %d: No articles from any source!", cycle_num)
                # Don't count as complete failure if at least one source worked
                if all(r.get("status") == "error" for r in source_results.values()):
                    return False

            # 2. Symbol assignment – fix any GENERAL tags
            for art in all_articles:
                if art.symbol == "GENERAL":
                    art.symbol = _assign_symbol(art.title + " " + (art.content or ""))

            # 3. Filter to tracked symbols + deduplicate
            unique = []
            for art in all_articles:
                if art.symbol not in self.symbols:
                    continue
                if not self._is_duplicate(art):
                    unique.append(art)
                    self._mark_seen(art)

            # Track which symbols got new articles (for incremental sentiment)
            updated_symbols = set(art.symbol for art in unique)

            logger.info("Dedup: %d raw → %d unique (symbols: %s)",
                       len(all_articles), len(unique), ", ".join(sorted(updated_symbols)) or "none")

            # 4. OPTIMIZED: Priority processing for breaking news + batch for rest
            processed = 0
            errors = 0

            if unique:
                # Separate breaking news from regular news
                breaking = [art for art in unique if _is_breaking_news(art)]
                regular = [art for art in unique if not _is_breaking_news(art)]

                # 4a. BREAKING NEWS: Process immediately (low latency path)
                if breaking:
                    logger.info("BREAKING NEWS: %d urgent articles detected, processing immediately...",
                               len(breaking))
                    breaking_start = datetime.now()
                    for art in breaking:
                        try:
                            analyzed_art = self.nlp.analyze(art)
                            self.db.save_article(analyzed_art)
                            processed += 1
                            logger.info("  [%s] BREAKING: %s… sentiment=%.2f",
                                       art.symbol, art.title[:60], analyzed_art.sentiment_score)
                        except Exception as e:
                            errors += 1
                            logger.error("Breaking news processing error: %s", e)
                    breaking_time = (datetime.now() - breaking_start).total_seconds()
                    logger.info("Breaking news: %d articles in %.1fs (%.0f ms/article)",
                               len(breaking), breaking_time,
                               breaking_time * 1000 / len(breaking) if breaking else 0)

                # 4b. REGULAR NEWS: Batch process (high throughput path)
                if regular:
                    try:
                        nlp_start = datetime.now()
                        analyzed = self.nlp.analyze_batch(regular, batch_size=8)
                        nlp_time = (datetime.now() - nlp_start).total_seconds()
                        logger.info("Batch NLP: %d articles in %.1fs (%.0f ms/article)",
                                   len(analyzed), nlp_time,
                                   nlp_time * 1000 / len(analyzed) if analyzed else 0)

                        # Bulk DB insert
                        db_start = datetime.now()
                        batch_saved = self.db.save_articles_bulk(analyzed)
                        processed += batch_saved
                        db_time = (datetime.now() - db_start).total_seconds()
                        logger.info("Bulk DB save: %d articles in %.2fs", batch_saved, db_time)

                        # Log sample articles
                        for art in analyzed[:3]:
                            logger.debug("[%s] %s… sentiment=%.2f",
                                        art.symbol, art.title[:50], art.sentiment_score)

                    except Exception as exc:
                        logger.error("Batch processing error: %s", exc)
                        errors += len(regular)
                        # Fallback to sequential
                        logger.info("Falling back to sequential processing...")
                        for art in regular:
                            try:
                                analyzed_art = self.nlp.analyze(art)
                                self.db.save_article(analyzed_art)
                                processed += 1
                                errors -= 1
                            except Exception as e:
                                if errors <= 3:
                                    logger.error("Sequential NLP/save error: %s", e)

            self.stats["unique_processed"] += processed
            self.stats["last_cycle_articles"] = processed

            if errors > 0:
                logger.warning("NLP processing: %d errors out of %d articles", errors, len(unique))

            # 5. OPTIMIZED: Only recompute sentiment for symbols with new articles
            if updated_symbols:
                logger.info("Computing sentiment factors for %d updated symbols…", len(updated_symbols))
                for sym in updated_symbols:
                    try:
                        factors = self.sentiment_factor.compute_factors(sym)
                        if factors.get("sentiment_level") != 0:
                            logger.info(
                                "  [%s] level=%.3f  mom=%.3f  var=%.3f",
                                sym,
                                factors["sentiment_level"],
                                factors["sentiment_momentum"],
                                factors["sentiment_variance"],
                            )
                    except Exception as exc:
                        logger.error("Sentiment factor error for %s: %s", sym, exc)
            else:
                logger.debug("No new articles, skipping sentiment factor computation")

            # 6. Periodic cleanup
            self._cleanup_dedup()

            # Success!
            duration = (datetime.now() - cycle_start).total_seconds()
            self.stats["last_cycle_time"] = datetime.now()
            self.stats["consecutive_failures"] = 0

            logger.info("-" * 60)
            logger.info("Cycle %d COMPLETE: %d articles scored in %.1fs",
                       cycle_num, processed, duration)
            logger.info("Sources: Google=%d, RSS=%d, DDG=%d",
                       source_results.get("google_rss", {}).get("count", 0),
                       source_results.get("rss_multi", {}).get("count", 0),
                       source_results.get("ddg", {}).get("count", 0))
            logger.info("-" * 60)

            self._write_health("healthy")
            return True

        except Exception as exc:
            logger.error("Cycle %d FAILED: %s", cycle_num, exc)
            logger.debug(traceback.format_exc())
            self.stats["consecutive_failures"] += 1
            self._write_health("error", str(exc))
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self):
        """Main run loop with resilience."""
        self.running = True
        self.stats["start_time"] = datetime.now()

        logger.info("=" * 70)
        logger.info("NEWS COLLECTOR SERVICE STARTED")
        logger.info("=" * 70)
        logger.info("Symbols  : %s", ", ".join(self.symbols))
        sources = "Google RSS | RSS Multi (8 feeds) | DuckDuckGo"
        if self.telegram:
            sources += " | Telegram (real-time)"
        logger.info("Sources  : %s", sources)
        logger.info("Interval : %d min", self.interval_minutes)
        logger.info("Dedup    : ID + URL + Jaccard (threshold %.2f)", self.DEDUP_JACCARD_THRESHOLD)
        logger.info("Health   : %s", HEALTH_FILE)
        logger.info("=" * 70)

        self._write_health("starting")

        # Immediate first cycle
        success = await self.fetch_and_process()
        if not success:
            logger.warning("First cycle failed, but continuing...")

        # Main loop
        while self.running:
            try:
                # Wait for interval or shutdown
                wait_start = datetime.now()
                logger.debug("Entering %d-minute wait (cycle %d complete)",
                           self.interval_minutes, self.stats["cycles"])
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.interval_minutes * 60
                    )
                    # If we get here, shutdown was requested
                    logger.info("Shutdown event received during wait")
                    break
                except asyncio.TimeoutError:
                    # Normal timeout - run next cycle
                    wait_duration = (datetime.now() - wait_start).total_seconds()
                    logger.debug("Wait completed after %.1fs, starting next cycle", wait_duration)

                if not self.running:
                    logger.info("Running flag is False, exiting loop")
                    break

                success = await self.fetch_and_process()

                if not success:
                    self.stats["consecutive_failures"] += 1
                    if self.stats["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
                        logger.error(
                            "Too many consecutive failures (%d). Stopping collector.",
                            self.stats["consecutive_failures"]
                        )
                        self._write_health("failed", "Too many consecutive failures")
                        break

                    # Back off on failures
                    backoff = min(60 * self.stats["consecutive_failures"], 300)
                    logger.warning("Backing off for %ds due to failure", backoff)
                    await asyncio.sleep(backoff)

            except asyncio.CancelledError:
                logger.info("Received cancellation signal")
                break
            except Exception as exc:
                logger.error("Unexpected error in main loop: %s", exc)
                logger.debug(traceback.format_exc())
                await asyncio.sleep(60)

        logger.info("News collector stopped.")
        self._print_stats()
        self._write_health("stopped")

    def _print_stats(self):
        runtime = datetime.now() - self.stats["start_time"] if self.stats["start_time"] else timedelta(0)
        logger.info("=" * 60)
        logger.info("FINAL STATISTICS")
        logger.info("=" * 60)
        logger.info("Runtime            : %s", runtime)
        logger.info("Cycles completed   : %d", self.stats["cycles"])
        logger.info("Total fetched      : %d", self.stats["total_fetched"])
        logger.info("Unique processed   : %d", self.stats["unique_processed"])
        logger.info("Source breakdown:")
        for source, data in self.stats["source_stats"].items():
            logger.info("  %-12s : %d fetched, %d failures",
                       source, data["fetched"], data["failures"])
        logger.info("=" * 60)

    def stop(self):
        """Signal graceful shutdown."""
        logger.info("Stopping news collector…")
        self.running = False
        self._shutdown_event.set()


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------
import atexit
import faulthandler

# Enable faulthandler to get tracebacks on segfaults
faulthandler.enable()


def _atexit_handler():
    """Log when process exits for debugging."""
    logger.warning("NEWS COLLECTOR PROCESS EXITING (atexit handler)")


atexit.register(_atexit_handler)


async def main():
    parser = argparse.ArgumentParser(description="Production News Collector")
    parser.add_argument(
        "--symbols", type=str,
        default="XAU,XAG,TSLA,NVDA,AMD,COIN,AAPL,GOOGL,MSFT,AMZN,META",
        help="Comma-separated symbols",
    )
    parser.add_argument(
        "--interval", type=int, default=5,
        help="Fetch interval in minutes (default: 5)",
    )
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    collector = NewsCollector(symbols=symbols, interval_minutes=args.interval)

    loop = asyncio.get_event_loop()

    def _sig_handler(signum):
        sig_name = signal.Signals(signum).name
        logger.warning("Received signal %s (%d)", sig_name, signum)
        collector.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: _sig_handler(s))

    # Also log SIGHUP if received (sometimes sent by terminal disconnect)
    try:
        loop.add_signal_handler(signal.SIGHUP, lambda: _sig_handler(signal.SIGHUP))
    except (ValueError, OSError):
        pass  # SIGHUP may not be available on all platforms

    try:
        logger.info("Starting collector main loop...")
        await collector.run()
        logger.info("Collector main loop ended normally")
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
        collector.stop()
    except Exception as exc:
        logger.error("Unhandled exception in main(): %s", exc)
        logger.error(traceback.format_exc())
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error("Fatal error: %s", e)
        logger.error(traceback.format_exc())
        sys.exit(1)
