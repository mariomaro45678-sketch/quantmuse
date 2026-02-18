"""
Dashboard Backend API - Phase 8
FastAPI application with REST endpoints and WebSocket for real-time updates.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

# Data service imports
from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher
from data_service.executors.hyperliquid_executor import HyperliquidExecutor
from data_service.executors.order_manager import OrderManager
from data_service.risk.risk_manager import RiskManager
from data_service.risk.position_sizer import PositionSizer
from data_service.ai.sentiment_factor import SentimentFactor
from data_service.storage.database_manager import DatabaseManager
from data_service.strategies.strategy_base import STRATEGY_REGISTRY
from data_service.utils.health_check import get_health
from data_service.utils.config_loader import get_config

logger = logging.getLogger(__name__)

# ============================================================================
# FastAPI App Setup
# ============================================================================

app = FastAPI(
    title="Hyperliquid Trading Dashboard",
    description="Real-time trading dashboard API",
    version="1.0.0"
)

# Shared instances (initialized on startup)
fetcher: Optional[HyperliquidFetcher] = None
executor: Optional[HyperliquidExecutor] = None
order_manager: Optional[OrderManager] = None
risk_manager: Optional[RiskManager] = None
position_sizer: Optional[PositionSizer] = None
sentiment_factor: Optional[SentimentFactor] = None
db_manager: Optional[DatabaseManager] = None
config = None

# Strategy state tracking
strategy_states: Dict[str, Dict[str, Any]] = {}

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket connected. Active: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Active: {len(self.active_connections)}")
    
    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        if not self.active_connections:
            return
        data = json.dumps(message)
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_text(data)
            except Exception:
                disconnected.add(connection)
        for conn in disconnected:
            self.active_connections.discard(conn)

ws_manager = ConnectionManager()

# Background task handle
_background_task: Optional[asyncio.Task] = None


# ============================================================================
# Startup / Shutdown
# ============================================================================

@app.on_event("startup")
async def startup():
    global fetcher, executor, order_manager, risk_manager, position_sizer
    global sentiment_factor, db_manager, config, strategy_states, _background_task
    
    logger.info("Starting Dashboard API...")
    
    # Initialize services
    config = get_config()
    db_manager = DatabaseManager()
    fetcher = HyperliquidFetcher(mode="mock")
    executor = HyperliquidExecutor(mode="mock")
    risk_manager = RiskManager(db_manager=db_manager)
    order_manager = OrderManager(executor=executor, risk_manager=risk_manager)
    position_sizer = PositionSizer(risk_manager=risk_manager)
    sentiment_factor = SentimentFactor(db_manager=db_manager)
    
    # Initialize strategy states from registry
    for name in STRATEGY_REGISTRY.keys():
        strategy_states[name] = {
            "name": name,
            "status": "stopped",
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "total_trades": 0,
            "last_signal": None
        }
    
    # Start background broadcast task
    _background_task = asyncio.create_task(broadcast_loop())
    
    logger.info("Dashboard API started successfully")


@app.on_event("shutdown")
async def shutdown():
    global _background_task
    if _background_task:
        _background_task.cancel()
        try:
            await _background_task
        except asyncio.CancelledError:
            pass
    logger.info("Dashboard API shutdown complete")


async def broadcast_loop():
    """Background loop for broadcasting real-time updates."""
    portfolio_interval = 1.0    # 1 Hz
    price_interval = 0.1        # 10 Hz
    alert_interval = 2.0        # 0.5 Hz
    
    last_portfolio = 0
    last_price = 0
    last_alert = 0
    max_alert_id = 0  # To track which alerts we've already sent
    
    symbols = ["XAU", "XAG", "TSLA", "NVDA", "AAPL", "GOOGL", "MSFT", "AMZN", "META", "AMD", "COIN"]
    
    while True:
        try:
            now = time.time()
            
            # 1. Portfolio update (1 Hz)
            if now - last_portfolio >= portfolio_interval:
                portfolio = build_portfolio_data()
                await ws_manager.broadcast({
                    "type": "portfolio_update",
                    "data": portfolio,
                    "timestamp": now
                })
                last_portfolio = now
            
            # 2. Price ticks (10 Hz, batched)
            if now - last_price >= price_interval:
                prices = {}
                for symbol in symbols:
                    try:
                        md = await fetcher.get_market_data(symbol)
                        prices[symbol] = {
                            "mid_price": md.mid_price,
                            "bid": md.bid,
                            "ask": md.ask
                        }
                    except Exception:
                        pass
                
                if prices:
                    await ws_manager.broadcast({
                        "type": "price_tick",
                        "data": prices,
                        "timestamp": now
                    })
                last_price = now

            # 3. New Alerts (0.5 Hz)
            if now - last_alert >= alert_interval:
                if db_manager:
                    try:
                        alerts = db_manager.get_recent_alerts(limit=5)
                        for alert in reversed(alerts):  # Send oldest first
                            if alert['id'] > max_alert_id:
                                await ws_manager.broadcast({
                                    "type": "risk_alert",
                                    "data": alert,
                                    "timestamp": alert['timestamp']
                                })
                                max_alert_id = alert['id']
                    except Exception as e:
                        logger.error(f"Error broadcasting alerts: {e}")
                last_alert = now
            
            await asyncio.sleep(0.05)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Broadcast loop error: {e}")
            await asyncio.sleep(1)


# ============================================================================
# Pydantic Models
# ============================================================================

class PortfolioResponse(BaseModel):
    total_equity: float
    leverage: float
    today_pnl: float
    unrealized_pnl: float
    num_positions: int

class PositionItem(BaseModel):
    symbol: str
    size: float
    entry_price: float
    mark_price: float
    pnl: float
    pnl_pct: float
    direction: str
    liquidation_price: Optional[float] = None
    liq_distance_pct: Optional[float] = None  # % distance from current price to liquidation

class TradeItem(BaseModel):
    id: int
    symbol: str
    side: str
    size: float
    price: float
    realized_pnl: float
    timestamp: str
    strategy: str

class CandleItem(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float

class RiskResponse(BaseModel):
    var_95: float
    var_99: float
    cvar_95: float
    max_drawdown: float
    leverage: float
    num_positions: int
    timestamp: str

class EquityPoint(BaseModel):
    time: int
    equity: float
    drawdown: float

class SentimentResponse(BaseModel):
    symbol: str
    sentiment_level: float
    sentiment_momentum: float
    sentiment_variance: float
    recent_articles: List[Dict[str, Any]]

class StrategyItem(BaseModel):
    name: str
    status: str
    sharpe_ratio: float
    max_drawdown: float
    total_trades: int
    last_signal: Optional[str]

class HealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    last_api_call: Optional[str]
    last_order: Optional[str]
    ws_connections: int


# ============================================================================
# Helper Functions
# ============================================================================

def build_portfolio_data() -> dict:
    """Build portfolio data from current state."""
    # Mock data for demonstration (in production, read from executor/manager)
    equity = 100000 + (time.time() % 1000) * 10  # Slight variance
    
    positions = executor.ledger.positions if hasattr(executor, 'ledger') else {}
    unrealized = sum(p.get('unrealized_pnl', 0) for p in positions.values())
    leverage = risk_manager.compute_leverage_ratio() if risk_manager else 0.0
    
    return {
        "total_equity": round(equity, 2),
        "leverage": round(leverage, 2),
        "today_pnl": round((time.time() % 500) - 250, 2),  # Mock
        "unrealized_pnl": round(unrealized, 2),
        "num_positions": len(positions)
    }


# ============================================================================
# REST Endpoints
# ============================================================================

@app.get("/api/portfolio", response_model=PortfolioResponse)
async def get_portfolio():
    """Get current portfolio overview."""
    data = build_portfolio_data()
    return PortfolioResponse(**data)


def calculate_liquidation_price(entry_price: float, direction: str, leverage: float = 10.0) -> tuple:
    """
    Calculate liquidation price based on position direction and leverage.
    Returns (liquidation_price, distance_percent_from_entry).

    For longs: liq_price = entry * (1 - 1/leverage + maintenance_margin)
    For shorts: liq_price = entry * (1 + 1/leverage - maintenance_margin)

    Using 0.5% maintenance margin typical for perpetuals.
    """
    maintenance_margin = 0.005  # 0.5%

    if direction == 'long':
        # Long gets liquidated when price drops
        liq_price = entry_price * (1 - (1 / leverage) + maintenance_margin)
    else:
        # Short gets liquidated when price rises
        liq_price = entry_price * (1 + (1 / leverage) - maintenance_margin)

    return liq_price


@app.get("/api/positions", response_model=List[PositionItem])
async def get_positions():
    """Get open positions table with liquidation prices."""
    positions = []

    # Get portfolio leverage for liquidation calculations
    portfolio_leverage = 5.0  # Default assumption
    if risk_manager:
        try:
            portfolio_leverage = max(risk_manager.compute_leverage_ratio(), 2.0)
        except:
            pass

    # Get positions from executor ledger
    if executor and hasattr(executor, 'ledger'):
        for symbol, pos in executor.ledger.positions.items():
            entry = pos.get('avg_entry_price', 0)
            size = pos.get('size', 0)
            direction = 'long' if size > 0 else 'short'

            # Get current price
            try:
                md = await fetcher.get_market_data(symbol)
                mark = md.mid_price
            except:
                mark = entry

            pnl = (mark - entry) * abs(size) * (1 if direction == 'long' else -1)
            pnl_pct = (pnl / (entry * abs(size))) * 100 if entry and size else 0

            # Calculate liquidation price
            liq_price = calculate_liquidation_price(entry, direction, portfolio_leverage)

            # Calculate distance to liquidation from current mark price
            if direction == 'long':
                liq_distance = ((mark - liq_price) / mark) * 100 if mark > 0 else 0
            else:
                liq_distance = ((liq_price - mark) / mark) * 100 if mark > 0 else 0

            positions.append(PositionItem(
                symbol=symbol,
                size=abs(size),
                entry_price=entry,
                mark_price=mark,
                pnl=round(pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                direction=direction,
                liquidation_price=round(liq_price, 2),
                liq_distance_pct=round(liq_distance, 2)
            ))

    # Add mock positions if empty (for demo)
    if not positions:
        # Mock data with realistic liquidation prices
        positions = [
            PositionItem(
                symbol="XAU", size=5.0, entry_price=1950.0, mark_price=1965.0,
                pnl=75.0, pnl_pct=0.77, direction="long",
                liquidation_price=1755.0, liq_distance_pct=10.7
            ),
            PositionItem(
                symbol="XAG", size=100.0, entry_price=23.50, mark_price=23.30,
                pnl=-20.0, pnl_pct=-0.85, direction="long",
                liquidation_price=21.15, liq_distance_pct=9.2
            ),
        ]

    return positions


@app.get("/api/trades")
async def get_trades(limit: int = Query(default=10, ge=1, le=100)):
    """Get recent trades."""
    trades = []
    
    # Get from order manager history
    if order_manager:
        history = order_manager.get_order_history(limit=limit)
        for i, order in enumerate(history):
            price = order.get('fill_price') or order.get('price') or 0.0
            trades.append(TradeItem(
                id=order.get('order_id', i),
                symbol=order.get('symbol', 'XAU'),
                side=order.get('side', 'buy'),
                size=order.get('size', 1.0),
                price=float(price),
                realized_pnl=order.get('realized_pnl') or 0.0,
                timestamp=order.get('created_at', datetime.now().isoformat()),
                strategy=order.get('strategy_name', 'manual')
            ))
    
    # Mock trades if empty
    if not trades:
        now = datetime.now()
        trades = [
            TradeItem(id=1, symbol="XAU", side="buy", size=2.0, price=1945.0, realized_pnl=0, timestamp=(now - timedelta(hours=1)).isoformat(), strategy="momentum_perpetuals"),
            TradeItem(id=2, symbol="XAG", side="buy", size=50.0, price=23.40, realized_pnl=0, timestamp=(now - timedelta(hours=2)).isoformat(), strategy="mean_reversion_metals"),
        ]
    
    return trades[:limit]


@app.get("/api/candles/{symbol}")
async def get_candles(
    symbol: str,
    timeframe: str = Query(default="1h"),
    limit: int = Query(default=200, ge=1, le=1000)
):
    """Get OHLCV candle data for charting."""
    try:
        df = await fetcher.get_candles(symbol, timeframe, limit=limit)
        candles = []
        
        for idx, row in df.iterrows():
            candles.append({
                "time": int(row.get('timestamp', idx) if 'timestamp' in df.columns else idx),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": float(row.get('volume', 0))
            })
        
        return candles
    except Exception as e:
        logger.error(f"Error fetching candles for {symbol}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/equity-history")
async def get_equity_history(
    period: str = Query(default="7d", regex="^(1d|7d|30d|90d|all)$")
):
    """Get historical equity curve data from risk_snapshots table."""
    if not db_manager:
        return {"points": [], "period": period}

    try:
        import sqlite3

        # Determine time range
        now = datetime.now()
        if period == "1d":
            start_time = now - timedelta(days=1)
        elif period == "7d":
            start_time = now - timedelta(days=7)
        elif period == "30d":
            start_time = now - timedelta(days=30)
        elif period == "90d":
            start_time = now - timedelta(days=90)
        else:  # all
            start_time = datetime(2020, 1, 1)

        with sqlite3.connect(db_manager.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, total_equity, max_drawdown
                FROM risk_snapshots
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
            """, (start_time.timestamp(),))

            rows = cursor.fetchall()

        # Track peak for drawdown calculation
        peak_equity = 0
        points = []

        for ts, equity, stored_dd in rows:
            equity = equity or 100000
            peak_equity = max(peak_equity, equity)
            current_dd = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else 0

            points.append({
                "time": int(ts),
                "equity": round(equity, 2),
                "drawdown": round(current_dd, 2)
            })

        # If no data, generate mock data for visualization
        if not points:
            base_equity = 100000
            for i in range(100):
                t = int((now - timedelta(hours=100-i)).timestamp())
                # Simulate slight growth with noise
                equity = base_equity + (i * 10) + ((i * 7) % 50) - 25
                dd = max(0, (100000 - equity) / 100000 * 100) if equity < 100000 else 0
                points.append({
                    "time": t,
                    "equity": round(equity, 2),
                    "drawdown": round(dd, 2)
                })

        return {
            "points": points,
            "period": period,
            "peak_equity": round(peak_equity, 2) if peak_equity > 0 else 100000,
            "current_equity": points[-1]["equity"] if points else 100000,
            "max_drawdown": max((p["drawdown"] for p in points), default=0)
        }

    except Exception as e:
        logger.error(f"Error getting equity history: {e}")
        return {"points": [], "period": period, "error": str(e)}


@app.get("/api/risk", response_model=RiskResponse)
async def get_risk():
    """Get latest risk metrics snapshot."""
    import math
    
    def safe_float(val, default=0.0):
        """Convert to float, handling NaN and None."""
        if val is None:
            return default
        try:
            f = float(val)
            return default if math.isnan(f) or math.isinf(f) else f
        except (TypeError, ValueError):
            return default
    
    if risk_manager:
        snapshot = risk_manager.get_risk_snapshot()
        # Convert float timestamp to ISO string
        ts = snapshot.get('timestamp', time.time())
        ts_str = datetime.fromtimestamp(ts).isoformat() if isinstance(ts, (int, float)) else str(ts)
        return RiskResponse(
            var_95=safe_float(snapshot.get('var_95')),
            var_99=safe_float(snapshot.get('var_99')),
            cvar_95=safe_float(snapshot.get('cvar_95')),
            max_drawdown=safe_float(snapshot.get('max_drawdown')),
            leverage=safe_float(snapshot.get('total_leverage')),
            num_positions=snapshot.get('num_positions', 0),
            timestamp=ts_str
        )
    
    # Fallback mock
    return RiskResponse(
        var_95=-0.0165,
        var_99=-0.0231,
        cvar_95=-0.0198,
        max_drawdown=0.035,
        leverage=2.5,
        num_positions=2,
        timestamp=datetime.now().isoformat()
    )


@app.get("/api/sentiment/{symbol}")
async def get_sentiment(symbol: str):
    """Get sentiment factors and recent articles for a symbol."""
    factors = {"sentiment_level": 0.0, "sentiment_momentum": 0.0, "sentiment_variance": 0.0}
    articles = []
    
    if sentiment_factor:
        try:
            factors = sentiment_factor.get_factors(symbol)
        except Exception as e:
            logger.warning(f"Error getting sentiment for {symbol}: {e}")
    
    if db_manager:
        try:
            raw_articles = db_manager.get_recent_articles(symbol, hours_back=24)
            for art in raw_articles[:10]:
                articles.append({
                    "id": art.id,
                    "title": art.title,
                    "source": art.source,
                    "sentiment_score": art.sentiment_score,
                    "published_at": art.published_at.isoformat() if hasattr(art.published_at, 'isoformat') else str(art.published_at)
                })
        except Exception as e:
            logger.warning(f"Error getting articles for {symbol}: {e}")
    
    # Mock articles if empty
    if not articles:
        now = datetime.now()
        articles = [
            {"id": "1", "title": "Gold prices surge on Fed comments", "source": "Reuters", "sentiment_score": 0.75, "published_at": (now - timedelta(hours=1)).isoformat()},
            {"id": "2", "title": "Silver demand remains strong", "source": "Bloomberg", "sentiment_score": 0.45, "published_at": (now - timedelta(hours=3)).isoformat()},
        ]
    
    return {
        "symbol": symbol,
        "sentiment_level": factors.get("sentiment_level", 0),
        "sentiment_momentum": factors.get("sentiment_momentum", 0),
        "sentiment_variance": factors.get("sentiment_variance", 0),
        "recent_articles": articles
    }


@app.get("/api/strategies", response_model=List[StrategyItem])
async def get_strategies():
    """Get list of strategies with status and metrics."""
    return [StrategyItem(**state) for state in strategy_states.values()]


@app.post("/api/strategies/{name}/start")
async def start_strategy(name: str):
    """Start a strategy."""
    if name not in strategy_states:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    
    strategy_states[name]["status"] = "running"
    logger.info(f"Strategy started: {name}")
    
    # Broadcast status change
    await ws_manager.broadcast({
        "type": "strategy_status",
        "data": {"name": name, "status": "running"},
        "timestamp": time.time()
    })
    
    return {"success": True, "message": f"Strategy '{name}' started"}


@app.post("/api/strategies/{name}/stop")
async def stop_strategy(name: str):
    """Stop a strategy."""
    if name not in strategy_states:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    
    strategy_states[name]["status"] = "stopped"
    logger.info(f"Strategy stopped: {name}")
    
    # Broadcast status change
    await ws_manager.broadcast({
        "type": "strategy_status",
        "data": {"name": name, "status": "stopped"},
        "timestamp": time.time()
    })
    
    return {"success": True, "message": f"Strategy '{name}' stopped"}


@app.get("/api/health", response_model=HealthResponse)
async def get_health_check():
    """Get system health check data."""
    health = get_health()

    # Check if system is healthy (no errors in last hour)
    is_healthy = health.total_errors == 0 or health.uptime_seconds() < 60

    return HealthResponse(
        status="healthy" if is_healthy else "degraded",
        uptime_seconds=health.uptime_seconds(),
        last_api_call=datetime.fromtimestamp(health.last_api_call).isoformat() if health.last_api_call else None,
        last_order=datetime.fromtimestamp(health.last_order_placed).isoformat() if health.last_order_placed else None,
        ws_connections=len(ws_manager.active_connections)
    )


# ============================================================================
# Funding Rates API
# ============================================================================

@app.get("/api/funding-rates")
async def get_funding_rates():
    """Get current funding rates for perpetual futures."""
    # Symbols to fetch funding rates for
    perp_symbols = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "MATIC", "LINK"]

    rates = []
    if fetcher:
        for symbol in perp_symbols:
            try:
                # Get recent funding history (last 24h)
                history = await fetcher.get_funding_history(symbol, days=1)
                if history:
                    # Get most recent rate
                    latest = history[-1] if history else None
                    if latest:
                        # Calculate 8h rate (standard) and annualized
                        rate_8h = latest.rate
                        rate_annual = rate_8h * 3 * 365 * 100  # 3 funding periods per day

                        # Determine if you pay or receive
                        # Positive rate: longs pay shorts
                        # Negative rate: shorts pay longs
                        rates.append({
                            "symbol": symbol,
                            "rate_8h": rate_8h,
                            "rate_8h_pct": rate_8h * 100,
                            "rate_annual_pct": rate_annual,
                            "direction": "pay" if rate_8h > 0 else "receive",
                            "timestamp": latest.time
                        })
            except Exception as e:
                logger.debug(f"Error fetching funding for {symbol}: {e}")

    # Sort by absolute rate (highest first)
    rates.sort(key=lambda x: abs(x.get("rate_8h", 0)), reverse=True)

    return {
        "rates": rates,
        "timestamp": datetime.now().isoformat()
    }


# ============================================================================
# Metals Factors API
# ============================================================================

@app.get("/api/metals-factors")
async def get_metals_factors():
    """Get metals ratio factors for mean reversion strategy signals."""
    factors = {
        "gold_silver_ratio": None,
        "gold_silver_zscore": None,
        "copper_gold_ratio": None,
        "industrial_momentum": None,
        "signal": "neutral",
        "signal_strength": 0,
        "history": []
    }

    if db_manager:
        try:
            # Get latest metals factors from database
            import sqlite3
            conn = sqlite3.connect(db_manager.db_path)
            cur = conn.cursor()

            # Get latest record
            cur.execute("""
                SELECT timestamp, gold_silver_ratio, gold_silver_ratio_zscore,
                       copper_gold_ratio, industrial_basket_momentum
                FROM metals_factors
                ORDER BY timestamp DESC
                LIMIT 1
            """)
            row = cur.fetchone()

            if row:
                factors["gold_silver_ratio"] = row[1]
                factors["gold_silver_zscore"] = row[2]
                factors["copper_gold_ratio"] = row[3]
                factors["industrial_momentum"] = row[4]
                factors["timestamp"] = row[0]

                # Determine signal based on z-score
                zscore = row[2] or 0
                if zscore > 2:
                    factors["signal"] = "silver_undervalued"
                    factors["signal_strength"] = min((zscore - 2) / 2, 1)  # 0-1 scale
                elif zscore < -2:
                    factors["signal"] = "gold_undervalued"
                    factors["signal_strength"] = min((-zscore - 2) / 2, 1)
                else:
                    factors["signal"] = "neutral"
                    factors["signal_strength"] = 0

            # Get historical data for chart (last 24 hours)
            cur.execute("""
                SELECT timestamp, gold_silver_ratio, gold_silver_ratio_zscore
                FROM metals_factors
                WHERE timestamp > datetime('now', '-24 hours')
                ORDER BY timestamp ASC
            """)
            history_rows = cur.fetchall()
            factors["history"] = [
                {"time": r[0], "ratio": r[1], "zscore": r[2]}
                for r in history_rows
            ]

            conn.close()
        except Exception as e:
            logger.warning(f"Error fetching metals factors: {e}")

    return factors


@app.get("/api/phase13/status")
async def get_phase13_status():
    """Get Phase 13 paper trading status for both tracks."""
    import subprocess
    
    # Check running processes
    testnet_running = False
    mock_running = False
    
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        for line in result.stdout.split('\n'):
            if 'main.py' in line and 'testnet' in line:
                testnet_running = True
            if 'main.py' in line and 'mock' in line:
                mock_running = True
    except:
        pass
    
    # Query database for trade statistics
    def get_track_stats(strategy_filter: str):
        """Get stats for a specific track."""
        if not db_manager:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "last_trade_time": None
            }
        
        try:
            import sqlite3
            with sqlite3.connect(db_manager.db_path) as conn:
                cursor = conn.cursor()

                # Get trade stats
                cursor.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
                        SUM(realized_pnl) as total_pnl,
                        AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END) as avg_win,
                        AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END) as avg_loss,
                        MAX(created_at) as last_trade
                    FROM trades
                    WHERE strategy_name LIKE ?
                """, (f'%{strategy_filter}%',))

                row = cursor.fetchone()

                total = row[0] or 0
                wins = row[1] or 0
                losses = row[2] or 0

                return {
                    "total_trades": total,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": (wins / total * 100) if total > 0 else 0.0,
                    "total_pnl": round(row[3] or 0.0, 2),
                    "avg_win": round(row[4] or 0.0, 2),
                    "avg_loss": round(row[5] or 0.0, 2),
                    "last_trade_time": row[6]
                }
        except Exception as e:
            logger.error(f"Error getting track stats: {e}")
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "last_trade_time": None
            }
    
    # Get stats for each track
    testnet_stats = get_track_stats("momentum")
    mock_stats = get_track_stats("reversion")
    
    # Calculate cumulative stats
    cumulative_pnl = testnet_stats["total_pnl"] + mock_stats["total_pnl"]
    cumulative_trades = testnet_stats["total_trades"] + mock_stats["total_trades"]
    cumulative_wins = testnet_stats["wins"] + mock_stats["wins"]
    
    return {
        "testnet_crypto": {
            "status": "running" if testnet_running else "stopped",
            "assets": ["BTC", "ETH"],
            "strategy": "momentum_perpetuals",
            "stats": testnet_stats
        },
        "mock_metals": {
            "status": "running" if mock_running else "stopped",
            "assets": ["XAG", "XAU"],
            "strategy": "mean_reversion_metals",
            "stats": mock_stats
        },
        "cumulative": {
            "total_trades": cumulative_trades,
            "total_wins": cumulative_wins,
            "win_rate": (cumulative_wins / cumulative_trades * 100) if cumulative_trades > 0 else 0.0,
            "total_pnl": round(cumulative_pnl, 2),
            "return_pct": round((cumulative_pnl / 10000) * 100, 2)  # 10k total capital
        },
        "timestamp": datetime.now().isoformat()
    }



@app.get("/api/logs")
async def get_logs(
    level: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200)
):
    """Retrieve recent application logs."""
    log_file = Path("logs/app.log")
    if not log_file.exists():
        return []
    
    try:
        with open(log_file, "r") as f:
            # Read last lines (simplified, but works for reasonably sized logs)
            lines = f.readlines()[-limit*2:] # Read more to allow filtering
            
        logs = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            
            # Basic parsing (assumes "timestamp - name - level - message")
            parts = line.split(" - ", 3)
            if len(parts) >= 3:
                l_level = parts[2].strip()
                # Ensure level is a string before calling upper()
                target_level = level if isinstance(level, str) else None
                if target_level and l_level.upper() != target_level.upper():
                    continue
                
                logs.append({
                    "timestamp": parts[0],
                    "logger": parts[1],
                    "level": l_level,
                    "message": parts[3] if len(parts) > 3 else ""
                })
            
            if len(logs) >= limit:
                break
                
        return logs
    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        return []


# ============================================================================
# Trade Notification Endpoints
# ============================================================================

# In-memory trade notification queue (for SSE)
trade_notifications: List[Dict] = []
MAX_NOTIFICATIONS = 100


def add_trade_notification(trade_data: Dict):
    """Add a trade notification to the queue."""
    trade_notifications.append({
        **trade_data,
        "notification_time": datetime.now().isoformat()
    })
    # Keep only recent notifications
    while len(trade_notifications) > MAX_NOTIFICATIONS:
        trade_notifications.pop(0)


@app.get("/api/trades/stats")
async def get_trade_stats():
    """Get comprehensive mock trading statistics."""
    if executor and hasattr(executor, 'mock_ledger'):
        stats = executor.get_trade_stats()
        return {
            "total_trades": stats.get("total_trades", 0),
            "wins": stats.get("wins", 0),
            "losses": stats.get("losses", 0),
            "win_rate": round(stats.get("win_rate", 0) * 100, 2),
            "total_pnl": round(stats.get("total_pnl", 0), 2),
            "total_fees": round(stats.get("total_fees", 0), 2),
            "avg_slippage_bps": round(stats.get("avg_slippage_bps", 0), 2),
            "return_pct": round(stats.get("return_pct", 0), 2),
            "equity": round(executor.mock_ledger.equity, 2),
            "initial_equity": round(executor.mock_ledger.initial_equity, 2),
            "timestamp": datetime.now().isoformat()
        }

    return {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "total_fees": 0.0,
        "avg_slippage_bps": 0.0,
        "return_pct": 0.0,
        "equity": 100000.0,
        "initial_equity": 100000.0,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/trades/recent")
async def get_recent_trades(limit: int = Query(default=50, ge=1, le=200)):
    """Get recent mock trades with full details."""
    if executor and hasattr(executor, 'mock_ledger'):
        trades = executor.get_recent_trades(limit)
        return {
            "trades": [
                {
                    **t,
                    "timestamp_iso": datetime.fromtimestamp(t["timestamp"]).isoformat()
                }
                for t in trades
            ],
            "count": len(trades)
        }

    return {"trades": [], "count": 0}


@app.get("/api/trades/notifications")
async def get_trade_notifications(limit: int = Query(default=20, ge=1, le=100)):
    """Get recent trade notifications."""
    return {
        "notifications": trade_notifications[-limit:],
        "count": len(trade_notifications[-limit:])
    }


@app.get("/api/multi-strategy/status")
async def get_multi_strategy_status():
    """Get status of all running strategies including stocks."""
    import subprocess

    # Define strategy configurations
    strategy_configs = {
        "momentum_perpetuals": {
            "assets": ["TSLA", "NVDA", "AMD", "COIN"],
            "asset_class": "stocks",
            "description": "Momentum strategy for volatile stocks"
        },
        "mean_reversion_metals": {
            "assets": ["XAU", "XAG"],
            "asset_class": "metals",
            "description": "Mean reversion on gold/silver ratio"
        },
        "sentiment_driven": {
            "assets": ["AAPL", "GOOGL", "MSFT", "AMZN", "META"],
            "asset_class": "stocks",
            "description": "News sentiment-driven trading"
        }
    }

    # Check running processes
    running_strategies = set()
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        for line in result.stdout.split('\n'):
            if 'main.py' in line or 'run_multi_strategy' in line:
                for strategy in strategy_configs.keys():
                    if strategy in line.lower():
                        running_strategies.add(strategy)
                # Check for mock mode with any asset
                if 'mock' in line.lower():
                    if 'XAU' in line or 'XAG' in line:
                        running_strategies.add("mean_reversion_metals")
                    if any(s in line for s in ["TSLA", "NVDA", "AAPL"]):
                        running_strategies.add("momentum_perpetuals")
    except:
        pass

    # Get trade stats from mock ledger
    mock_stats = {}
    if executor and hasattr(executor, 'mock_ledger'):
        mock_stats = executor.get_trade_stats()

    strategies_status = []
    for name, cfg in strategy_configs.items():
        strategies_status.append({
            "name": name,
            "status": "running" if name in running_strategies else "stopped",
            "assets": cfg["assets"],
            "asset_class": cfg["asset_class"],
            "description": cfg["description"]
        })

    return {
        "strategies": strategies_status,
        "mock_trading": {
            "enabled": True,
            "equity": round(mock_stats.get("equity", executor.mock_ledger.equity if executor and hasattr(executor, 'mock_ledger') else 100000), 2),
            "total_trades": mock_stats.get("total_trades", 0),
            "total_pnl": round(mock_stats.get("total_pnl", 0), 2),
            "return_pct": round(mock_stats.get("return_pct", 0), 2)
        },
        "timestamp": datetime.now().isoformat()
    }


# ============================================================================
# WebSocket Endpoint
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, receive any client messages
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Handle client subscriptions if needed
                logger.debug(f"WS received: {data}")
            except asyncio.TimeoutError:
                # Send ping to keep alive
                await websocket.send_json({"type": "ping", "timestamp": time.time()})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)


# ============================================================================
# Static Files & Root
# ============================================================================

# Mount static files for dashboard
static_path = Path(__file__).parent.parent / "web" / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/")
async def serve_dashboard():
    """Serve the dashboard HTML."""
    html_path = static_path / "dashboard.html"
    if html_path.exists():
        return FileResponse(html_path)
    return {"message": "Dashboard not found. Please create web/static/dashboard.html"}


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
    )
    uvicorn.run(app, host="0.0.0.0", port=8001)
