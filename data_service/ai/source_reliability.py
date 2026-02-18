"""
Source Reliability Scoring Module

Tracks per-source metrics to dynamically weight news sentiment:
- hit_rate: % of signals followed by correct price direction
- avg_return: average return when trading on source's signals
- latency_score: how early vs other sources (first-mover advantage)

Score = 0.5 * hit_rate + 0.3 * normalized_return + 0.2 * latency_score
Weight = 0.5 + (reliability_score * 0.5)  # Range: 0.5x to 1.0x
"""

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import sqlite3

from data_service.storage.database_manager import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass
class SourceMetrics:
    """Reliability metrics for a single news source."""
    source_name: str
    total_signals: int
    correct_signals: int
    hit_rate: float
    total_return: float
    avg_return: float
    avg_latency_rank: float  # 1.0 = always first, higher = slower
    latency_score: float
    reliability_score: float
    weight: float
    last_updated: datetime


class SourceReliabilityTracker:
    """
    Tracks and computes reliability scores for news sources.

    Uses trade outcomes to measure how well each source predicts direction.
    """

    # Minimum signals needed before using dynamic weights
    MIN_SIGNALS_FOR_SCORING = 10

    # Default weights for sources without enough history
    DEFAULT_WEIGHTS = {
        "telegram": 1.0,
        "reuters": 0.95,
        "investing.com": 0.90,
        "cnbc": 0.85,
        "yahoo": 0.85,
        "google": 0.80,
        "duckduckgo": 0.80,
        "rss": 0.80,
    }

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()
        self._cache: Dict[str, SourceMetrics] = {}
        self._cache_expiry: Optional[datetime] = None
        self._cache_ttl = timedelta(hours=1)

        self._ensure_table()

    def _ensure_table(self):
        """Create source_reliability table if it doesn't exist."""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS source_reliability (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT UNIQUE,
                    total_signals INTEGER DEFAULT 0,
                    correct_signals INTEGER DEFAULT 0,
                    total_return REAL DEFAULT 0.0,
                    latency_sum REAL DEFAULT 0.0,
                    last_updated TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source_name
                ON source_reliability(source_name)
            """)
            conn.commit()

    def _normalize_source(self, source: str) -> str:
        """Normalize source name for consistent matching."""
        s = source.lower().strip()
        # Extract main source identifier
        if "telegram" in s:
            return "telegram"
        if "reuters" in s:
            return "reuters"
        if "investing.com" in s or "investing" in s:
            return "investing.com"
        if "cnbc" in s:
            return "cnbc"
        if "yahoo" in s:
            return "yahoo"
        if "duckduckgo" in s or "ddg" in s:
            return "duckduckgo"
        if "google" in s:
            return "google"
        if "rss" in s:
            return "rss"
        if "marketwatch" in s:
            return "marketwatch"
        if "coindesk" in s:
            return "coindesk"
        if "fxstreet" in s:
            return "fxstreet"
        return s

    def record_signal_outcome(
        self,
        source: str,
        symbol: str,
        signal_direction: str,  # 'long' or 'short'
        entry_price: float,
        exit_price: float,
        signal_time: datetime,
        other_sources_times: Optional[List[datetime]] = None
    ):
        """
        Record the outcome of a trade signal from a source.

        Args:
            source: The news source name
            symbol: Trading symbol
            signal_direction: 'long' or 'short'
            entry_price: Entry price of trade
            exit_price: Exit price of trade
            signal_time: When this source reported the news
            other_sources_times: Times when other sources reported same news
        """
        source_key = self._normalize_source(source)

        # Calculate if direction was correct
        if signal_direction == 'long':
            is_correct = exit_price > entry_price
            pnl_pct = (exit_price - entry_price) / entry_price
        else:  # short
            is_correct = exit_price < entry_price
            pnl_pct = (entry_price - exit_price) / entry_price

        # Calculate latency rank (1 = first, 2 = second, etc.)
        latency_rank = 1.0
        if other_sources_times:
            earlier_count = sum(1 for t in other_sources_times if t < signal_time)
            latency_rank = 1.0 + earlier_count

        # Update database
        with sqlite3.connect(self.db.db_path) as conn:
            # Get current stats
            row = conn.execute(
                "SELECT total_signals, correct_signals, total_return, latency_sum "
                "FROM source_reliability WHERE source_name = ?",
                (source_key,)
            ).fetchone()

            if row:
                total = row[0] + 1
                correct = row[1] + (1 if is_correct else 0)
                total_ret = row[2] + pnl_pct
                latency_sum = row[3] + latency_rank

                conn.execute(
                    """UPDATE source_reliability
                       SET total_signals = ?, correct_signals = ?,
                           total_return = ?, latency_sum = ?, last_updated = ?
                       WHERE source_name = ?""",
                    (total, correct, total_ret, latency_sum, datetime.now(), source_key)
                )
            else:
                conn.execute(
                    """INSERT INTO source_reliability
                       (source_name, total_signals, correct_signals, total_return,
                        latency_sum, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (source_key, 1, 1 if is_correct else 0, pnl_pct,
                     latency_rank, datetime.now())
                )

            conn.commit()

        # Invalidate cache
        self._cache_expiry = None

        logger.debug(f"Recorded outcome for {source_key}: correct={is_correct}, "
                    f"pnl={pnl_pct:.4f}, latency_rank={latency_rank}")

    def compute_metrics(self, source_name: str) -> Optional[SourceMetrics]:
        """Compute reliability metrics for a single source."""
        source_key = self._normalize_source(source_name)

        with sqlite3.connect(self.db.db_path) as conn:
            row = conn.execute(
                """SELECT total_signals, correct_signals, total_return,
                          latency_sum, last_updated
                   FROM source_reliability WHERE source_name = ?""",
                (source_key,)
            ).fetchone()

        if not row or row[0] < self.MIN_SIGNALS_FOR_SCORING:
            return None

        total_signals = row[0]
        correct_signals = row[1]
        total_return = row[2]
        latency_sum = row[3]
        last_updated = datetime.fromisoformat(row[4]) if row[4] else datetime.now()

        # Calculate metrics
        hit_rate = correct_signals / total_signals
        avg_return = total_return / total_signals
        avg_latency_rank = latency_sum / total_signals

        # Normalize return to 0-1 range (assume -5% to +5% range)
        normalized_return = max(0, min(1, (avg_return + 0.05) / 0.10))

        # Latency score: 1st place = 1.0, 2nd = 0.8, 3rd = 0.6, etc.
        latency_score = max(0, 1.0 - (avg_latency_rank - 1) * 0.2)

        # Combined reliability score
        reliability_score = (
            0.5 * hit_rate +
            0.3 * normalized_return +
            0.2 * latency_score
        )

        # Weight: 0.5 to 1.0 based on reliability
        weight = 0.5 + (reliability_score * 0.5)

        return SourceMetrics(
            source_name=source_key,
            total_signals=total_signals,
            correct_signals=correct_signals,
            hit_rate=hit_rate,
            total_return=total_return,
            avg_return=avg_return,
            avg_latency_rank=avg_latency_rank,
            latency_score=latency_score,
            reliability_score=reliability_score,
            weight=weight,
            last_updated=last_updated
        )

    def get_all_metrics(self, force_refresh: bool = False) -> Dict[str, SourceMetrics]:
        """Get metrics for all tracked sources."""
        now = datetime.now()

        # Check cache
        if (not force_refresh and
            self._cache_expiry and
            now < self._cache_expiry):
            return self._cache

        # Fetch all sources
        with sqlite3.connect(self.db.db_path) as conn:
            rows = conn.execute(
                "SELECT source_name FROM source_reliability"
            ).fetchall()

        self._cache = {}
        for (source_name,) in rows:
            metrics = self.compute_metrics(source_name)
            if metrics:
                self._cache[source_name] = metrics

        self._cache_expiry = now + self._cache_ttl
        return self._cache

    def get_source_weight(self, source: str) -> float:
        """
        Get the dynamic weight for a source.

        Falls back to default weights if source has insufficient history.

        Args:
            source: The news source name

        Returns:
            Weight multiplier (0.5 to 1.0 for dynamic, or default)
        """
        source_key = self._normalize_source(source)

        # Check cache first
        metrics = self.get_all_metrics()
        if source_key in metrics:
            return metrics[source_key].weight

        # Fall back to defaults
        for key, weight in self.DEFAULT_WEIGHTS.items():
            if key in source_key or source_key in key:
                return weight

        return 0.85  # Safe default for unknown sources

    def get_reliability_summary(self) -> Dict[str, Dict]:
        """Get a summary of all source reliability scores for display."""
        metrics = self.get_all_metrics(force_refresh=True)

        summary = {}
        for source, m in metrics.items():
            summary[source] = {
                "total_signals": m.total_signals,
                "hit_rate": f"{m.hit_rate:.1%}",
                "avg_return": f"{m.avg_return:+.2%}",
                "latency_score": f"{m.latency_score:.2f}",
                "reliability": f"{m.reliability_score:.2f}",
                "weight": f"{m.weight:.2f}x",
            }

        # Add defaults for sources not yet tracked
        for source, default_weight in self.DEFAULT_WEIGHTS.items():
            if source not in summary:
                summary[source] = {
                    "total_signals": 0,
                    "hit_rate": "N/A",
                    "avg_return": "N/A",
                    "latency_score": "N/A",
                    "reliability": "default",
                    "weight": f"{default_weight:.2f}x (default)",
                }

        return summary

    def backfill_from_trades(
        self,
        lookback_days: int = 30,
        min_hold_minutes: int = 5
    ) -> int:
        """
        Backfill reliability data from historical trades and news.

        Matches sentiment-driven trades to news articles that triggered them.

        Args:
            lookback_days: How far back to look
            min_hold_minutes: Minimum trade duration to consider

        Returns:
            Number of signal-outcome pairs recorded
        """
        cutoff = datetime.now() - timedelta(days=lookback_days)
        recorded = 0

        with sqlite3.connect(self.db.db_path) as conn:
            # Get sentiment-driven trades with PnL
            trades = conn.execute(
                """SELECT symbol, side, created_at, closed_at,
                          price, fill_price, realized_pnl
                   FROM trades
                   WHERE strategy_name = 'sentiment_driven'
                     AND created_at > ?
                     AND realized_pnl IS NOT NULL
                     AND closed_at IS NOT NULL
                   ORDER BY created_at""",
                (cutoff.isoformat(),)
            ).fetchall()

            for trade in trades:
                symbol, side, created_at, closed_at, entry_price, fill_price, pnl = trade

                if not entry_price or not fill_price:
                    continue

                created_dt = datetime.fromisoformat(created_at)

                # Find news articles in the 2 hours before trade
                articles = conn.execute(
                    """SELECT source, published_at FROM news
                       WHERE symbol = ?
                         AND published_at BETWEEN ? AND ?
                       ORDER BY published_at""",
                    (symbol,
                     (created_dt - timedelta(hours=2)).isoformat(),
                     created_dt.isoformat())
                ).fetchall()

                if not articles:
                    continue

                # Record outcome for each source
                article_times = [datetime.fromisoformat(a[1]) for a in articles]

                for source, pub_at in articles:
                    pub_dt = datetime.fromisoformat(pub_at)
                    other_times = [t for t in article_times if t != pub_dt]

                    self.record_signal_outcome(
                        source=source,
                        symbol=symbol,
                        signal_direction='long' if side.lower() == 'buy' else 'short',
                        entry_price=entry_price,
                        exit_price=fill_price,
                        signal_time=pub_dt,
                        other_sources_times=other_times
                    )
                    recorded += 1

        logger.info(f"Backfilled {recorded} signal outcomes from {len(trades)} trades")
        return recorded


# Singleton instance for easy access
_tracker: Optional[SourceReliabilityTracker] = None


def get_reliability_tracker() -> SourceReliabilityTracker:
    """Get or create the singleton reliability tracker."""
    global _tracker
    if _tracker is None:
        _tracker = SourceReliabilityTracker()
    return _tracker
