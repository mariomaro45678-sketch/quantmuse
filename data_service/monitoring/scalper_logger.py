"""
ScalperLogger - Detailed monitoring system for the Enhanced Scalper strategy
=============================================================================

Captures every significant event from the Enhanced Scalper in two sinks:
  1. Rotating daily log file: logs/scalper_YYYYMMDD.log  (human-readable)
  2. SQLite table: scalper_events (structured, queryable)

Event categories:
  - SIGNAL     : Signal generated (direction, confidence, rationale, symbol)
  - CONFLICT   : Position conflict detected with another strategy
  - COOLDOWN   : Symbol skipped due to cooldown
  - ENTRY      : Trade entry (price, size, direction, confidence)
  - EXIT       : Trade exit (reason: stop/take-profit/time/breakeven, P&L)
  - RISK       : Risk event (consecutive losses, circuit breaker, max exposure cap)
  - REGIME     : Market regime information at signal time
  - ORDERBOOK  : Order book snapshot metrics (OBI, spread, liquidity)
  - PERF       : Rolling performance snapshot (win rate, avg hold, Sharpe estimate)
  - CYCLE      : Per-cycle summary (signals generated, skips, execution time)

Usage:
    logger = ScalperLogger()
    logger.log_signal("BTC", "long", 0.78, "Bullish momentum; High volume", price=95000)
    logger.log_entry("BTC", "long", 0.78, 95000, 0.01)
    logger.log_exit("BTC", 95570, entry_price=95000, direction="long", reason="take_profit")
    logger.log_cycle_summary(signals_count=2, skipped_count=1, cycle_ms=350)
    print(logger.get_performance_summary())
"""

import logging
import logging.handlers
import sqlite3
import json
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from collections import deque

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    """Tracks a single completed trade for performance calculations."""
    symbol: str
    direction: str        # 'long' | 'short'
    entry_price: float
    exit_price: float
    size: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: str      # 'stop_loss' | 'take_profit' | 'time_stop' | 'breakeven'
    pnl_pct: float        # Signed percentage P&L
    hold_seconds: float


@dataclass
class CycleStats:
    """Aggregated stats for a single scalper cycle."""
    timestamp: datetime
    symbols_analyzed: int = 0
    signals_long: int = 0
    signals_short: int = 0
    signals_flat: int = 0
    skipped_conflict: int = 0
    skipped_cooldown: int = 0
    skipped_low_conf: int = 0
    cycle_ms: float = 0.0


# ---------------------------------------------------------------------------
# ScalperLogger
# ---------------------------------------------------------------------------

class ScalperLogger:
    """
    Dedicated logging and monitoring system for the Enhanced Scalper strategy.

    Thread-safe. Can be used from async code (writes are non-blocking via
    a background thread queue, but for simplicity we use a Lock here since
    SQLite writes are fast).
    """

    # Rolling window for performance stats
    MAX_TRADE_HISTORY = 200

    def __init__(self,
                 log_dir: Path = Path("logs"),
                 db_path: Path = Path("hyperliquid.db"),
                 paper_trading: bool = True):

        self.log_dir = log_dir
        self.db_path = db_path
        self.paper_trading = paper_trading
        self._lock = threading.Lock()

        # In-memory trade history for rolling stats
        self._trades: deque = deque(maxlen=self.MAX_TRADE_HISTORY)

        # Open position tracking {symbol: {entry_price, direction, size, entry_time}}
        self._open_positions: Dict[str, Dict[str, Any]] = {}

        # --- Set up dedicated file logger ---
        self._file_logger = self._setup_file_logger()

        # --- Set up SQLite table ---
        self._init_db()

        mode_tag = "[PAPER]" if paper_trading else "[LIVE]"
        self._file_logger.info(
            f"ScalperLogger initialized {mode_tag} | "
            f"DB: {db_path} | LogDir: {log_dir}"
        )

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _setup_file_logger(self) -> logging.Logger:
        """Create a dedicated rotating daily file logger for the scalper."""
        self.log_dir.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger("scalper_monitor")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False  # Don't bubble up to root logger

        # Remove old handlers if re-initialised
        logger.handlers.clear()

        # Daily rotating file: logs/scalper_YYYYMMDD.log
        today = date.today().strftime("%Y%m%d")
        log_file = self.log_dir / f"scalper_{today}.log"

        file_handler = logging.handlers.TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=14,       # Keep 2 weeks of history
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(file_handler)

        # Also mirror to console at INFO level for real-time visibility
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(
            "[SCALPER] %(levelname)-7s | %(message)s"
        ))
        logger.addHandler(console_handler)

        return logger

    def _init_db(self):
        """Create the scalper_events table if it doesn't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scalper_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    event_type  TEXT NOT NULL,
                    symbol      TEXT,
                    direction   TEXT,
                    confidence  REAL,
                    price       REAL,
                    size        REAL,
                    pnl_pct     REAL,
                    hold_sec    REAL,
                    exit_reason TEXT,
                    obi         REAL,
                    spread_pct  REAL,
                    liquidity   REAL,
                    vol_ratio   REAL,
                    regime      TEXT,
                    details     TEXT,
                    paper_mode  INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_scalper_ts
                ON scalper_events(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_scalper_type
                ON scalper_events(event_type)
            """)
            conn.commit()

    def _db_insert(self, event_type: str, **kwargs):
        """Insert a row into scalper_events. Non-raising."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cols = ["timestamp", "event_type", "paper_mode"]
                vals = [datetime.now().isoformat(), event_type,
                        1 if self.paper_trading else 0]

                allowed = {
                    "symbol", "direction", "confidence", "price", "size",
                    "pnl_pct", "hold_sec", "exit_reason", "obi",
                    "spread_pct", "liquidity", "vol_ratio", "regime", "details"
                }
                for k, v in kwargs.items():
                    if k in allowed:
                        cols.append(k)
                        vals.append(v if not isinstance(v, dict) else json.dumps(v))

                placeholders = ", ".join("?" * len(vals))
                col_names = ", ".join(cols)
                conn.execute(
                    f"INSERT INTO scalper_events ({col_names}) VALUES ({placeholders})",
                    vals
                )
                conn.commit()
        except Exception as e:
            self._file_logger.warning(f"DB insert failed ({event_type}): {e}")

    # ------------------------------------------------------------------
    # Public logging methods
    # ------------------------------------------------------------------

    def log_signal(self, symbol: str, direction: str, confidence: float,
                   rationale: str, price: float = 0.0,
                   obi: float = None, spread_pct: float = None,
                   liquidity: float = None, vol_ratio: float = None,
                   regime: str = None):
        """Log a generated signal."""
        mode_tag = "[PAPER]" if self.paper_trading else "[LIVE!]"
        with self._lock:
            self._file_logger.info(
                f"SIGNAL {mode_tag} | {symbol:6s} {direction.upper():5s} "
                f"conf={confidence:.2f} | px={price:.2f} | {rationale}"
            )
            if direction != 'flat':
                details = {"rationale": rationale}
                if obi is not None:
                    details["obi"] = round(obi, 4)
                if spread_pct is not None:
                    details["spread_pct"] = round(spread_pct, 4)
                if liquidity is not None:
                    details["liquidity"] = round(liquidity, 4)
                if vol_ratio is not None:
                    details["vol_ratio"] = round(vol_ratio, 4)

                self._db_insert(
                    "SIGNAL",
                    symbol=symbol, direction=direction, confidence=confidence,
                    price=price, obi=obi, spread_pct=spread_pct,
                    liquidity=liquidity, vol_ratio=vol_ratio,
                    regime=regime, details=json.dumps(details)
                )

    def log_flat_signal(self, symbol: str, reason: str):
        """Log a flat (no-trade) signal with reason for auditability."""
        with self._lock:
            self._file_logger.debug(
                f"SIGNAL | {symbol:6s} FLAT   | {reason}"
            )

    def log_conflict(self, symbol: str, blocking_strategy: str,
                     blocking_position: float):
        """Log position conflict prevention."""
        with self._lock:
            self._file_logger.info(
                f"CONFLICT | {symbol:6s} blocked by [{blocking_strategy}] "
                f"pos={blocking_position:+.4f}"
            )
            self._db_insert(
                "CONFLICT", symbol=symbol,
                details=json.dumps({
                    "blocking_strategy": blocking_strategy,
                    "blocking_position": blocking_position
                })
            )

    def log_cooldown(self, symbol: str, remaining_seconds: float):
        """Log cooldown skip."""
        with self._lock:
            self._file_logger.debug(
                f"COOLDOWN | {symbol:6s} cooldown {remaining_seconds:.0f}s remaining"
            )

    def log_entry(self, symbol: str, direction: str, confidence: float,
                  price: float, size: float,
                  obi: float = None, vol_ratio: float = None):
        """Log a trade entry. Also starts tracking the open position."""
        mode_tag = "[PAPER]" if self.paper_trading else "[LIVE!]"
        with self._lock:
            self._open_positions[symbol] = {
                "direction": direction,
                "entry_price": price,
                "size": size,
                "entry_time": datetime.now(),
                "confidence": confidence,
            }
            self._file_logger.info(
                f"ENTRY   {mode_tag} | {symbol:6s} {direction.upper():5s} "
                f"@ {price:.4f} | size={size:.6f} | conf={confidence:.2f}"
            )
            self._db_insert(
                "ENTRY",
                symbol=symbol, direction=direction, confidence=confidence,
                price=price, size=size, obi=obi, vol_ratio=vol_ratio
            )

    def log_exit(self, symbol: str, exit_price: float,
                 entry_price: float, direction: str,
                 reason: str, size: float = None):
        """
        Log a trade exit. Calculates P&L and hold time from tracked entry.
        Updates rolling trade history for performance stats.
        """
        mode_tag = "[PAPER]" if self.paper_trading else "[LIVE!]"
        with self._lock:
            entry_info = self._open_positions.pop(symbol, None)

            if entry_info:
                actual_entry = entry_info["entry_price"]
                actual_direction = entry_info["direction"]
                actual_size = size or entry_info["size"]
                entry_time = entry_info["entry_time"]
            else:
                actual_entry = entry_price
                actual_direction = direction
                actual_size = size or 0.0
                entry_time = datetime.now()

            now = datetime.now()
            hold_sec = (now - entry_time).total_seconds()

            if actual_direction == 'long':
                pnl_pct = (exit_price - actual_entry) / actual_entry
            else:
                pnl_pct = (actual_entry - exit_price) / actual_entry

            pnl_sign = "+" if pnl_pct >= 0 else ""
            self._file_logger.info(
                f"EXIT    {mode_tag} | {symbol:6s} {actual_direction.upper():5s} "
                f"entry={actual_entry:.4f} exit={exit_price:.4f} "
                f"pnl={pnl_sign}{pnl_pct:.3%} | hold={hold_sec:.0f}s | {reason}"
            )

            # Record for performance stats
            trade = TradeRecord(
                symbol=symbol,
                direction=actual_direction,
                entry_price=actual_entry,
                exit_price=exit_price,
                size=actual_size,
                entry_time=entry_time,
                exit_time=now,
                exit_reason=reason,
                pnl_pct=pnl_pct,
                hold_seconds=hold_sec
            )
            self._trades.append(trade)

            self._db_insert(
                "EXIT",
                symbol=symbol, direction=actual_direction,
                price=exit_price, size=actual_size,
                pnl_pct=pnl_pct, hold_sec=hold_sec, exit_reason=reason,
                details=json.dumps({
                    "entry_price": actual_entry,
                    "exit_price": exit_price
                })
            )

    def log_risk_event(self, event: str, details: Dict[str, Any]):
        """Log a risk management event (circuit breaker, consecutive losses, etc.)."""
        with self._lock:
            self._file_logger.warning(
                f"RISK    | {event} | {json.dumps(details)}"
            )
            self._db_insert(
                "RISK",
                details=json.dumps({"event": event, **details})
            )

    def log_orderbook(self, symbol: str, obi: float, spread_pct: float,
                      liquidity_score: float, bid_depth_usd: float = None,
                      ask_depth_usd: float = None):
        """Log order book snapshot metrics at signal time."""
        with self._lock:
            self._file_logger.debug(
                f"ORDERBOOK | {symbol:6s} OBI={obi:+.3f} spread={spread_pct:.3f}% "
                f"liq={liquidity_score:.2f}"
                + (f" bid_depth=${bid_depth_usd:,.0f}" if bid_depth_usd else "")
                + (f" ask_depth=${ask_depth_usd:,.0f}" if ask_depth_usd else "")
            )
            self._db_insert(
                "ORDERBOOK",
                symbol=symbol, obi=obi, spread_pct=spread_pct,
                liquidity=liquidity_score,
                details=json.dumps({
                    "bid_depth_usd": bid_depth_usd,
                    "ask_depth_usd": ask_depth_usd
                })
            )

    def log_regime(self, symbol: str, regime: str, confidence: float,
                   multiplier: float):
        """Log regime state at signal evaluation time."""
        with self._lock:
            self._file_logger.debug(
                f"REGIME  | {symbol:6s} regime={regime} "
                f"conf={confidence:.2f} mult={multiplier:.2f}x"
            )

    def log_cycle_summary(self, symbols_analyzed: int, signals_long: int,
                          signals_short: int, signals_flat: int,
                          skipped_conflict: int, skipped_cooldown: int,
                          skipped_low_conf: int, cycle_ms: float):
        """Log a per-cycle summary of what the scalper evaluated and decided."""
        active_signals = signals_long + signals_short
        with self._lock:
            self._file_logger.info(
                f"CYCLE   | analyzed={symbols_analyzed} "
                f"signals=(L:{signals_long} S:{signals_short} flat:{signals_flat}) "
                f"skips=(conflict:{skipped_conflict} cooldown:{skipped_cooldown} "
                f"low_conf:{skipped_low_conf}) "
                f"cycle={cycle_ms:.0f}ms"
            )
            self._db_insert(
                "CYCLE",
                details=json.dumps({
                    "symbols_analyzed": symbols_analyzed,
                    "signals_long": signals_long,
                    "signals_short": signals_short,
                    "signals_flat": signals_flat,
                    "active_signals": active_signals,
                    "skipped_conflict": skipped_conflict,
                    "skipped_cooldown": skipped_cooldown,
                    "skipped_low_conf": skipped_low_conf,
                    "cycle_ms": round(cycle_ms, 1)
                })
            )

    # ------------------------------------------------------------------
    # Performance analytics
    # ------------------------------------------------------------------

    def get_performance_summary(self) -> Dict[str, Any]:
        """
        Compute rolling performance metrics from recent trade history.
        Returns dict suitable for logging or dashboard display.
        """
        with self._lock:
            trades = list(self._trades)

        if not trades:
            return {
                "trade_count": 0,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "avg_hold_sec": 0.0,
                "total_pnl_pct": 0.0,
                "sharpe_estimate": 0.0,
                "by_exit_reason": {},
                "by_symbol": {},
                "open_positions": list(self._open_positions.keys())
            }

        pnls = [t.pnl_pct for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        # Simple Sharpe estimate using mean/std of per-trade PnL
        import statistics
        avg_pnl = statistics.mean(pnls)
        std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
        sharpe = (avg_pnl / std_pnl) if std_pnl > 0 else 0.0

        # By exit reason breakdown
        by_reason: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            r = t.exit_reason
            if r not in by_reason:
                by_reason[r] = {"count": 0, "pnl_sum": 0.0}
            by_reason[r]["count"] += 1
            by_reason[r]["pnl_sum"] += t.pnl_pct

        # By symbol breakdown
        by_symbol: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            s = t.symbol
            if s not in by_symbol:
                by_symbol[s] = {"count": 0, "pnl_sum": 0.0, "wins": 0}
            by_symbol[s]["count"] += 1
            by_symbol[s]["pnl_sum"] += t.pnl_pct
            if t.pnl_pct > 0:
                by_symbol[s]["wins"] += 1

        return {
            "trade_count": len(trades),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": len(wins) / len(trades) if trades else 0.0,
            "avg_pnl_pct": avg_pnl,
            "avg_win_pct": statistics.mean(wins) if wins else 0.0,
            "avg_loss_pct": statistics.mean(losses) if losses else 0.0,
            "total_pnl_pct": sum(pnls),
            "sharpe_estimate": round(sharpe, 3),
            "avg_hold_sec": statistics.mean([t.hold_seconds for t in trades]),
            "max_consecutive_losses": self._max_consecutive_losses(trades),
            "by_exit_reason": by_reason,
            "by_symbol": by_symbol,
            "open_positions": list(self._open_positions.keys()),
            "window_trades": len(trades)
        }

    def log_performance_snapshot(self):
        """Log a formatted performance summary to the file logger."""
        perf = self.get_performance_summary()
        n = perf["trade_count"]
        if n == 0:
            self._file_logger.info("PERF    | No completed trades yet")
            return

        self._file_logger.info(
            f"PERF    | trades={n} win_rate={perf['win_rate']:.1%} "
            f"avg_pnl={perf['avg_pnl_pct']:+.3%} total_pnl={perf['total_pnl_pct']:+.3%} "
            f"sharpe={perf['sharpe_estimate']:.2f} "
            f"avg_hold={perf['avg_hold_sec']:.0f}s "
            f"max_consec_loss={perf['max_consecutive_losses']}"
        )
        # Per-symbol breakdown
        for sym, stats in perf["by_symbol"].items():
            sym_wr = stats["wins"] / stats["count"] if stats["count"] > 0 else 0
            self._file_logger.info(
                f"  {sym:6s}: {stats['count']:3d} trades | "
                f"win_rate={sym_wr:.1%} | "
                f"total_pnl={stats['pnl_sum']:+.3%}"
            )

        self._db_insert(
            "PERF",
            details=json.dumps({
                k: round(v, 6) if isinstance(v, float) else v
                for k, v in perf.items()
                if not isinstance(v, (dict, list))
            })
        )

    @staticmethod
    def _max_consecutive_losses(trades: List[TradeRecord]) -> int:
        """Calculate max consecutive losses in trade history."""
        max_streak = 0
        streak = 0
        for t in trades:
            if t.pnl_pct <= 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        return max_streak

    # ------------------------------------------------------------------
    # DB query helpers for dashboard / reporting
    # ------------------------------------------------------------------

    def query_recent_events(self, event_type: str = None,
                            hours: int = 24,
                            limit: int = 100) -> List[Dict[str, Any]]:
        """Query recent events from the DB."""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                if event_type:
                    rows = conn.execute("""
                        SELECT * FROM scalper_events
                        WHERE event_type = ? AND timestamp > ?
                        ORDER BY timestamp DESC LIMIT ?
                    """, (event_type, since, limit)).fetchall()
                else:
                    rows = conn.execute("""
                        SELECT * FROM scalper_events
                        WHERE timestamp > ?
                        ORDER BY timestamp DESC LIMIT ?
                    """, (since, limit)).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            self._file_logger.warning(f"DB query failed: {e}")
            return []

    def get_daily_stats_from_db(self, target_date: date = None) -> Dict[str, Any]:
        """
        Aggregate daily stats from scalper_events table.
        Returns counts, win rate, total PnL for the given date.
        """
        if target_date is None:
            target_date = date.today()
        date_str = target_date.isoformat()

        try:
            with sqlite3.connect(self.db_path) as conn:
                # Count by event type
                rows = conn.execute("""
                    SELECT event_type, COUNT(*) as cnt
                    FROM scalper_events
                    WHERE date(timestamp) = ?
                    GROUP BY event_type
                """, (date_str,)).fetchall()
                counts = {r[0]: r[1] for r in rows}

                # Exit stats
                exits = conn.execute("""
                    SELECT exit_reason, pnl_pct
                    FROM scalper_events
                    WHERE event_type = 'EXIT' AND date(timestamp) = ?
                      AND pnl_pct IS NOT NULL
                """, (date_str,)).fetchall()

                pnls = [r[1] for r in exits]
                wins = sum(1 for p in pnls if p > 0)
                total_pnl = sum(pnls)
                win_rate = wins / len(pnls) if pnls else 0.0

                return {
                    "date": date_str,
                    "event_counts": counts,
                    "trades": len(pnls),
                    "wins": wins,
                    "win_rate": round(win_rate, 4),
                    "total_pnl_pct": round(total_pnl, 6),
                    "exit_breakdown": {r[0]: 0 for r in exits}
                }
        except Exception as e:
            self._file_logger.warning(f"Daily stats query failed: {e}")
            return {}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_scalper_logger_instance: Optional[ScalperLogger] = None
_instance_lock = threading.Lock()


def get_scalper_logger(paper_trading: bool = True,
                       log_dir: Path = Path("logs"),
                       db_path: Path = Path("hyperliquid.db")) -> ScalperLogger:
    """
    Get or create the module-level ScalperLogger singleton.
    Call once at startup with paper_trading=True/False.
    Subsequent calls with no args return the cached instance.
    """
    global _scalper_logger_instance
    with _instance_lock:
        if _scalper_logger_instance is None:
            _scalper_logger_instance = ScalperLogger(
                log_dir=log_dir,
                db_path=db_path,
                paper_trading=paper_trading
            )
        return _scalper_logger_instance
