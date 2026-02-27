"""
Order Storage - Persists trading history to SQLite.
"""

import sqlite3
import logging
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Lazy import for source reliability
_reliability_tracker = None


def _get_reliability_tracker():
    """Lazy load reliability tracker to avoid circular imports."""
    global _reliability_tracker
    if _reliability_tracker is None:
        try:
            from data_service.ai.source_reliability import get_reliability_tracker
            _reliability_tracker = get_reliability_tracker()
        except ImportError:
            pass
    return _reliability_tracker

class OrderStorage:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path("hyperliquid.db")
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        """Create a connection with busy_timeout to prevent 'database is locked' errors."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self):
        """Initialize SQLite database and create trades table."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER UNIQUE,
                    symbol TEXT,
                    side TEXT,
                    order_type TEXT,
                    size REAL,
                    price REAL,
                    status TEXT,
                    strategy_name TEXT,
                    created_at TIMESTAMP,
                    closed_at TIMESTAMP,
                    fill_price REAL,
                    realized_pnl REAL,
                    raw_data TEXT
                )
            """)
            conn.commit()

    def save_order(self, order_data: Dict[str, Any]):
        """Save or update an order in the trades table."""
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Simplified "upsert" for SQLite
            cursor.execute("SELECT id FROM trades WHERE order_id = ?", (order_data.get('order_id'),))
            result = cursor.fetchone()
            
            if result:
                # Update existing order
                cursor.execute("""
                    UPDATE trades SET
                        status = ?,
                        closed_at = ?,
                        fill_price = ?,
                        realized_pnl = ?,
                        raw_data = ?
                    WHERE order_id = ?
                """, (
                    order_data.get('status'),
                    order_data.get('closed_at'),
                    order_data.get('fill_price'),
                    order_data.get('realized_pnl'),
                    json.dumps(order_data),
                    order_data.get('order_id')
                ))

                # If trade closed with PnL, update source reliability
                if (order_data.get('strategy_name') == 'sentiment_driven' and
                    order_data.get('realized_pnl') is not None and
                    order_data.get('closed_at')):
                    self._record_reliability_outcome(order_data, conn)
            else:
                # Insert new order
                cursor.execute("""
                    INSERT INTO trades (
                        order_id, symbol, side, order_type, size, price, 
                        status, strategy_name, created_at, raw_data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order_data.get('order_id'),
                    order_data.get('symbol'),
                    order_data.get('side'),
                    order_data.get('order_type'),
                    order_data.get('size'),
                    order_data.get('price'),
                    order_data.get('status'),
                    order_data.get('strategy_name'),
                    order_data.get('created_at'),
                    json.dumps(order_data)
                ))
            conn.commit()

    def _record_reliability_outcome(self, order_data: Dict[str, Any], conn):
        """Record trade outcome for source reliability tracking."""
        tracker = _get_reliability_tracker()
        if not tracker:
            return

        try:
            symbol = order_data.get('symbol')
            side = order_data.get('side', '').lower()
            entry_price = order_data.get('price')
            exit_price = order_data.get('fill_price')
            created_at = order_data.get('created_at')

            if not all([symbol, side, entry_price, exit_price, created_at]):
                return

            # Parse created_at
            if isinstance(created_at, str):
                created_dt = datetime.fromisoformat(created_at)
            else:
                created_dt = created_at

            # Find news articles in the 2 hours before trade
            cursor = conn.cursor()
            cursor.execute(
                """SELECT source, published_at FROM news
                   WHERE symbol = ?
                     AND published_at BETWEEN ? AND ?
                   ORDER BY published_at""",
                (symbol,
                 (created_dt - timedelta(hours=2)).isoformat(),
                 created_dt.isoformat())
            )
            articles = cursor.fetchall()

            if not articles:
                return

            article_times = [datetime.fromisoformat(a[1]) for a in articles]

            for source, pub_at in articles:
                pub_dt = datetime.fromisoformat(pub_at)
                other_times = [t for t in article_times if t != pub_dt]

                tracker.record_signal_outcome(
                    source=source,
                    symbol=symbol,
                    signal_direction='long' if side == 'buy' else 'short',
                    entry_price=entry_price,
                    exit_price=exit_price,
                    signal_time=pub_dt,
                    other_sources_times=other_times
                )

            logger.debug(f"Recorded reliability outcome for {symbol} from {len(articles)} sources")

        except Exception as e:
            logger.warning(f"Failed to record reliability outcome: {e}")

    def get_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve recent trade history."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
