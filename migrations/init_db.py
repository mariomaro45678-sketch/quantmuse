import sqlite3
import os
import logging
from pathlib import Path

# Setup simple logging for migration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = "hyperliquid.db"

def init_db():
    """Initialize the unified database with all required tables and indexes."""
    logger.info(f"Initializing database at {DB_PATH}...")
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # 1. Candles table
        logger.info("Creating 'candles' table...")
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
        
        # 2. News table
        logger.info("Creating 'news' table...")
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
        
        # 3. Trades (Orders) table
        logger.info("Creating 'trades' table...")
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
        
        # 4. Risk Snapshots table
        logger.info("Creating 'risk_snapshots' table...")
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
        
        # 5. Sentiment Factors table
        logger.info("Creating 'sentiment_factors' table...")
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
        
        # 6. Metals Factors table
        logger.info("Creating 'metals_factors' table...")
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
        
        # 7. Optimisation Results table
        logger.info("Creating 'optimisation_results' table...")
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
        
        # 8. Alerts table
        logger.info("Creating 'alerts' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                type TEXT,
                message TEXT,
                severity TEXT
            )
        """)
        
        # Create Indexes for performance
        logger.info("Creating indexes...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_candles_symbol_ts ON candles(symbol, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_symbol_pub ON news(symbol, published_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy_time ON trades(strategy_name, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_risk_ts ON risk_snapshots(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_factors_sym_ts ON sentiment_factors(symbol, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_type_ts ON alerts(type, timestamp)")
        
        conn.commit()
        logger.info("Database initialization complete.")

if __name__ == "__main__":
    init_db()
