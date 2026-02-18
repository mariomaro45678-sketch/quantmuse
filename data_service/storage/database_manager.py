import sqlite3
import logging
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from data_service.ai.sources.base_source import Article

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            # Use unified database name as per Phase 10 spec
            db_path = Path("hyperliquid.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """
        Initialize SQLite database. 
        Note: migrations/init_db.py should be the primary way to create the schema.
        This provides a safety check for the app.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Use the same schema as migrations/init_db.py
            # News table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    id TEXT PRIMARY KEY,
                    symbol TEXT,
                    title TEXT,
                    content TEXT,
                    source TEXT,
                    published_at TIMESTAMP,
                    sentiment_score REAL,
                    raw_data TEXT
                )
            """)
            
            # Sentiment factors snapshot table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sentiment_factors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    timestamp TIMESTAMP,
                    sentiment_level REAL,
                    sentiment_momentum REAL,
                    sentiment_variance REAL,
                    article_count INTEGER
                )
            """)

            # Metals factors table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metals_factors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP,
                    gold_silver_ratio REAL,
                    gold_silver_ratio_zscore REAL,
                    copper_gold_ratio REAL,
                    industrial_basket_momentum REAL
                )
            """)
            
            # Optimisation results table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS optimisation_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT,
                    timestamp TIMESTAMP,
                    parameters TEXT,
                    sharpe REAL,
                    max_drawdown REAL,
                    win_rate REAL,
                    total_return REAL,
                    score REAL,
                    is_oos INTEGER
                )
            """)

            # Risk snapshots table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS risk_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    total_equity REAL,
                    total_leverage REAL,
                    var_95 REAL,
                    var_99 REAL,
                    cvar_95 REAL,
                    max_drawdown REAL,
                    num_positions INTEGER
                )
            """)

            # Alerts table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    type TEXT,
                    message TEXT,
                    severity TEXT
                )
            """)
            
            # Candles table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT,
                    timeframe TEXT,
                    timestamp INTEGER,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    PRIMARY KEY (symbol, timeframe, timestamp)
                )
            """)
            
            # Indexing for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_symbol ON news(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_factors_symbol ON sentiment_factors(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metals_timestamp ON metals_factors(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_opt_strategy ON optimisation_results(strategy_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_risk_timestamp ON risk_snapshots(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_candles_symbol_ts ON candles(symbol, timestamp)")
            
            conn.commit()

    def save_article(self, article: Article):
        """Persist a scored article to the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO news (
                    id, symbol, title, content, source, published_at, sentiment_score, raw_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                article.id,
                article.symbol,
                article.title,
                article.content,
                article.source,
                article.published_at.isoformat(),
                article.sentiment_score,
                json.dumps(article.raw_data) if article.raw_data else None
            ))
            conn.commit()

    def save_articles_bulk(self, articles: List[Article]) -> int:
        """
        Bulk insert articles in a single transaction.
        Much faster than individual inserts - O(1) connection overhead.

        Args:
            articles: List of analyzed articles to save

        Returns:
            Number of articles saved
        """
        if not articles:
            return 0

        data = []
        for article in articles:
            data.append((
                article.id,
                article.symbol,
                article.title,
                article.content,
                article.source,
                article.published_at.isoformat() if article.published_at else None,
                article.sentiment_score,
                json.dumps(article.raw_data) if article.raw_data else None
            ))

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany("""
                INSERT OR REPLACE INTO news (
                    id, symbol, title, content, source, published_at, sentiment_score, raw_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, data)
            conn.commit()

        logger.debug(f"Bulk saved {len(articles)} articles")
        return len(articles)

    def get_recent_articles(self, symbol: str, hours_back: int = 24) -> List[Article]:
        """Fetch articles for a symbol within the specified time window."""
        cutoff = (datetime.now() - timedelta(hours=hours_back)).isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM news 
                WHERE symbol = ? AND published_at >= ?
                ORDER BY published_at DESC
            """, (symbol, cutoff))
            
            rows = cursor.fetchall()
            articles = []
            for row in rows:
                articles.append(Article(
                    id=row['id'],
                    symbol=row['symbol'],
                    title=row['title'],
                    content=row['content'],
                    source=row['source'],
                    published_at=datetime.fromisoformat(row['published_at']),
                    sentiment_score=row['sentiment_score'],
                    raw_data=json.loads(row['raw_data']) if row['raw_data'] else None
                ))
            return articles

    def save_sentiment_snapshot(self, symbol: str, factors: Dict[str, float], article_count: int):
        """Save a snapshot of computed sentiment factors."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sentiment_factors (
                    symbol, timestamp, sentiment_level, sentiment_momentum, sentiment_variance, article_count
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                datetime.now().isoformat(),
                factors.get('sentiment_level', 0.0),
                factors.get('sentiment_momentum', 0.0),
                factors.get('sentiment_variance', 0.0),
                article_count
            ))
            conn.commit()

    def get_latest_sentiment_factors(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get the most recent factors for a symbol."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM sentiment_factors
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (symbol,))

            row = cursor.fetchone()
            return dict(row) if row else None

    def get_sentiment_factors_near_timestamp(self, symbol: str, target_ts: datetime) -> Optional[Dict[str, Any]]:
        """Get sentiment factors closest to (but not after) a specific timestamp.

        Used for momentum calculation to compare current sentiment vs N hours ago.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM sentiment_factors
                WHERE symbol = ? AND timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (symbol, target_ts.isoformat()))

            row = cursor.fetchone()
            return dict(row) if row else None

    def save_metals_snapshot(self, factors: Dict[str, float]):
        """Save a snapshot of computed metals factors."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO metals_factors (
                    timestamp, gold_silver_ratio, gold_silver_ratio_zscore, 
                    copper_gold_ratio, industrial_basket_momentum
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                factors.get('gold_silver_ratio'),
                factors.get('gold_silver_ratio_zscore'),
                factors.get('copper_gold_ratio'),
                factors.get('industrial_basket_momentum')
            ))
            conn.commit()

    def get_latest_metals_factors(self) -> Optional[Dict[str, Any]]:
        """Get the most recent metals ratios."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM metals_factors ORDER BY timestamp DESC LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None

    def save_historical_metals_ratios(self, ratios: List[Dict[str, Any]]):
        """Save a list of historical metals ratios."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for r in ratios:
                cursor.execute("""
                    INSERT INTO metals_factors (
                        timestamp, gold_silver_ratio, gold_silver_ratio_zscore, 
                        copper_gold_ratio, industrial_basket_momentum
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    r.get('timestamp'),
                    r.get('gold_silver_ratio'),
                    r.get('gold_silver_ratio_zscore'),
                    r.get('copper_gold_ratio'),
                    r.get('industrial_basket_momentum')
                ))
            conn.commit()

    def save_optimisation_result(self, result: Dict[str, Any]):
        """Persist an optimisation run result."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO optimisation_results (
                    strategy_name, timestamp, parameters, sharpe, max_drawdown, win_rate, total_return, score, is_oos
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.get('strategy_name'),
                result.get('timestamp') or datetime.now().isoformat(),
                json.dumps(result.get('parameters')),
                result.get('sharpe'),
                result.get('max_drawdown'),
                result.get('win_rate'),
                result.get('total_return'),
                result.get('score'),
                1 if result.get('is_oos') else 0
            ))
            conn.commit()

    def save_optimization_summary(self, summary: Dict[str, Any]):
        """Persist an optimization summary."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO optimisation_summaries (
                    strategy_name, timestamp, mode, summary
                ) VALUES (?, ?, ?, ?)
            """, (
                summary.get('strategy_name'),
                summary.get('timestamp') or datetime.now().isoformat(),
                summary.get('mode'),
                json.dumps(summary.get('summary'))
            ))
            conn.commit()

    def save_risk_snapshot(self, snapshot: Dict[str, Any]):
        """Save a risk snapshot for Phase 7 risk management."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO risk_snapshots (
                    timestamp, total_equity, total_leverage, var_95, var_99, cvar_95, max_drawdown, num_positions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot.get('timestamp', datetime.now().timestamp()),
                snapshot.get('total_equity'),
                snapshot.get('total_leverage'),
                snapshot.get('var_95'),
                snapshot.get('var_99'),
                snapshot.get('cvar_95'),
                snapshot.get('max_drawdown'),
                snapshot.get('num_positions')
            ))
            conn.commit()

    def get_recent_risk_snapshots(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent risk snapshots."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM risk_snapshots 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def save_alert(self, alert: Dict[str, Any]):
        """Save an alert (circuit breaker, risk limit, etc.)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO alerts (timestamp, type, message, severity)
                VALUES (?, ?, ?, ?)
            """, (
                alert.get('timestamp', datetime.now().timestamp()),
                alert.get('type'),
                alert.get('message'),
                alert.get('severity', 'warning')
            ))
            conn.commit()

    def get_recent_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent alerts."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM alerts 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def save_candle(self, symbol: str, timeframe: str, data: Dict[str, Any]):
        """Save a single candle to the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO candles (
                    symbol, timeframe, timestamp, open, high, low, close, volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                timeframe,
                data.get('time'),
                data.get('open'),
                data.get('high'),
                data.get('low'),
                data.get('close'),
                data.get('volume')
            ))
            conn.commit()

    def get_candles(self, symbol: str, timeframe: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Retrieve historical candles from the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp as time, open, high, low, close, volume 
                FROM candles 
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (symbol, timeframe, limit))
            
            rows = cursor.fetchall()
            # Return reversed to have chronological order (oldest first)
            return [dict(row) for row in reversed(rows)]
