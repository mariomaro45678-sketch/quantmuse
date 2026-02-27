#!/usr/bin/env python3
"""
Enhanced Scalper - Paper Trading Runner
========================================

Runs the enhanced_scalper strategy ONLY in mock/paper mode.
Designed to run alongside the live multi-strategy process without conflicts.

Key design choices for coexistence:
- Separate process lock file (won't conflict with run_multi_strategy.py)
- Mock executor only (no real orders)
- WAL mode on shared SQLite DB to avoid "database is locked" errors
- BTC/ETH asset universe (no overlap with live: TSLA/NVDA/AMD/COIN/XAU/XAG/AAPL/GOOGL/MSFT/AMZN/META)
- Reads live positions from exchange for conflict detection (read-only)

Usage:
    python scripts/run_scalper_paper.py [--duration HOURS] [--symbols BTC,ETH]
"""

import asyncio
import argparse
import fcntl
import logging
import os
import signal
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.utils.logging_config import setup_logging
from data_service.utils.config_loader import get_config
from data_service.storage.database_manager import DatabaseManager
from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher
from data_service.strategies.strategy_base import STRATEGY_REGISTRY
from data_service.risk.risk_manager import RiskManager
from data_service.risk.position_sizer import PositionSizer
from data_service.executors.order_manager import OrderManager
from data_service.executors.hyperliquid_executor import HyperliquidExecutor
from data_service.factors.regime_detector import RegimeDetector
from data_service.factors.correlation_tracker import CorrelationTracker

# Import the scalper strategy to trigger @register_strategy
from data_service.strategies.enhanced_scalper import EnhancedScalper  # noqa: F401

logger = logging.getLogger("ScalperPaper")

SCRIPT_DIR = Path(__file__).parent.parent
PID_FILE = SCRIPT_DIR / "logs" / "scalper_paper.pid"
LOCK_FILE = SCRIPT_DIR / "logs" / "scalper_paper.lock"

# Default scalper config
SCALPER_CONFIG = {
    "enabled": True,
    "paper_trading_only": True,
    "assets": ["BTC", "ETH"],
    "interval_seconds": 10,
    "description": "Microstructure-based scalping (10x leverage) [PAPER]",
}


def enable_wal_mode(db_path: str = "hyperliquid.db"):
    """Enable WAL mode on the shared database to prevent locking conflicts."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        logger.info(f"SQLite journal_mode set to: {mode}")
    except Exception as e:
        logger.warning(f"Could not set WAL mode: {e}")


class ProcessLock:
    """File-based singleton lock (copied from run_multi_strategy.py)."""

    def __init__(self, lock_path: Path, pid_path: Path):
        self.lock_path = lock_path
        self.pid_path = pid_path
        self._lock_fd = None

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_fd = open(self.lock_path, 'w')
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.pid_path.write_text(str(os.getpid()))
            logger.info(f"Process lock acquired (PID: {os.getpid()})")
            return True
        except BlockingIOError:
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None
            existing_pid = "unknown"
            if self.pid_path.exists():
                existing_pid = self.pid_path.read_text().strip()
            logger.error(f"Another scalper paper instance is running (PID: {existing_pid})")
            return False
        except Exception as e:
            logger.error(f"Failed to acquire lock: {e}")
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None
            return False

    def release(self):
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
            except Exception:
                pass
            self._lock_fd = None
        try:
            self.lock_path.unlink(missing_ok=True)
            self.pid_path.unlink(missing_ok=True)
        except Exception:
            pass
        logger.info("Process lock released")


class ScalperPaperRunner:
    """
    Standalone paper-trading runner for the enhanced scalper.

    Uses the same StrategyRunner logic from run_multi_strategy.py but
    simplified for a single strategy in mock mode.
    """

    def __init__(self, assets: list, equity: float = 100_000):
        self.assets = assets
        self.equity = equity
        self.running = False
        self.cycle_count = 0
        self.trade_count = 0

        # Core components - all in MOCK mode
        self.db = DatabaseManager()
        self.fetcher = HyperliquidFetcher(mode="mock")
        self.executor = HyperliquidExecutor(mode="mock")
        self.risk_mgr = RiskManager(db_manager=self.db)
        self.regime_detector = RegimeDetector()
        self.correlation_tracker = CorrelationTracker()

        # Position sizing and order management
        self.order_mgr = OrderManager(executor=self.executor, risk_manager=self.risk_mgr)
        self.pos_sizer = PositionSizer(risk_manager=self.risk_mgr)

        # Set initial equity
        self.risk_mgr.set_portfolio(equity=equity, open_positions=[])

        # Initialize strategy
        if 'enhanced_scalper' not in STRATEGY_REGISTRY:
            raise RuntimeError("enhanced_scalper not registered. Check imports.")
        self.strategy = STRATEGY_REGISTRY['enhanced_scalper']()
        self.strategy.set_paper_trading(True)

        # Trade cooldown tracking
        self.last_trade_time: Dict[str, datetime] = {}
        self.trade_cooldown_minutes = 2  # Fast cooldown for scalping

        # Regime tracking
        self.last_regime = None

        # Live position reader for conflict detection (read-only, connects to real exchange)
        self._live_executor = None
        try:
            self._live_executor = HyperliquidExecutor(mode="live")
            logger.info("Live position reader initialized for conflict detection")
        except Exception as e:
            logger.warning(f"Could not init live executor for conflict detection: {e}")

        logger.info(f"ScalperPaperRunner initialized: assets={assets}, equity=${equity:.2f}")

    async def _get_live_positions(self) -> Dict[str, Dict[str, float]]:
        """
        Read live positions from exchange (read-only) for conflict detection.
        Returns positions in the format expected by set_other_positions().
        """
        if not self._live_executor:
            return {}
        try:
            positions = await self._live_executor.get_positions()
            live_positions = {}
            for pos in positions:
                coin = pos.symbol
                if ':' in coin:
                    coin = coin.split(':')[1]
                if abs(pos.size) > 1e-8:
                    live_positions[coin] = pos.size
            if live_positions:
                # Wrap in a dict keyed by strategy name for conflict detection
                return {"live_strategies": live_positions}
        except Exception as e:
            logger.debug(f"Could not read live positions: {e}")
        return {}

    async def run(self, duration_hours: float = None):
        """Main scalper execution loop."""
        self.running = True
        start_time = datetime.now()

        logger.info("=" * 60)
        logger.info("ENHANCED SCALPER - PAPER TRADING STARTED")
        logger.info(f"Assets: {self.assets}")
        logger.info(f"Interval: {SCALPER_CONFIG['interval_seconds']}s")
        logger.info(f"Equity: ${self.equity:.2f} (simulated)")
        logger.info(f"Leverage: {self.strategy.leverage}x")
        logger.info(f"Max position: {self.strategy.max_position_pct:.0%}")
        logger.info(f"SL: {self.strategy.stop_loss_pct:.2%} / TP: {self.strategy.take_profit_pct:.2%}")
        logger.info(f"Min confidence: {self.strategy.min_confidence}")
        logger.info("=" * 60)

        while self.running:
            try:
                self.cycle_count += 1

                # 1. Fetch market data
                market_data = {}
                for sym in self.assets:
                    try:
                        df = await self.fetcher.get_candles(sym, timeframe='1h', limit=100)
                        if not df.empty:
                            market_data[sym] = df
                            self.executor.set_price(sym, df['close'].iloc[-1])
                    except Exception as e:
                        logger.warning(f"Error fetching {sym}: {e}")

                if not market_data:
                    await asyncio.sleep(10)
                    continue

                # 2. Regime detection
                regime_state = None
                try:
                    regime_state = self.regime_detector.get_portfolio_regime(market_data)
                    if regime_state.regime != self.last_regime:
                        logger.info(f"Regime change: {self.last_regime} -> {regime_state.regime.value} "
                                    f"(conf={regime_state.confidence:.2f}, ADX={regime_state.adx:.1f})")
                        self.last_regime = regime_state.regime
                except Exception as e:
                    logger.debug(f"Regime detection error: {e}")

                # 3. Read live positions for conflict detection
                try:
                    live_positions = await self._get_live_positions()
                    self.strategy.set_other_positions(live_positions)
                except Exception as e:
                    logger.debug(f"Live position read error: {e}")

                # 4. Calculate signals
                factors = {
                    'fetcher': self.fetcher,
                    'regime': regime_state,
                }
                try:
                    signals = await self.strategy.calculate_signals(market_data, factors)
                except Exception as e:
                    logger.error(f"Signal calculation error: {e}")
                    await asyncio.sleep(10)
                    continue

                # 5. Get current mock positions
                current_positions = {}
                try:
                    positions = await self.executor.get_positions()
                    for pos in positions:
                        coin = pos.symbol.split(':')[-1] if ':' in pos.symbol else pos.symbol
                        current_positions[coin] = pos.size
                except Exception as e:
                    logger.debug(f"Mock position fetch error: {e}")

                # 6. Size positions
                raw_positions = self.strategy.size_positions(signals, None)

                # 7. Execute orders (mock)
                for sym, target_pct in raw_positions.items():
                    try:
                        md = await self.fetcher.get_market_data(sym)
                        px = md.mid_price

                        target_size = target_pct * self.risk_mgr.equity / px
                        current_size = current_positions.get(sym, 0)
                        delta_size = target_size - current_size

                        # Determine if closing
                        is_closing = False
                        if current_size > 0 and delta_size < 0:
                            is_closing = True
                        elif current_size < 0 and delta_size > 0:
                            is_closing = True

                        delta_notional = abs(delta_size * px)
                        min_delta = 2.0 if is_closing else 5.0
                        if delta_notional < min_delta:
                            continue

                        # Cooldown (skip for closes)
                        if not is_closing:
                            last_trade = self.last_trade_time.get(sym)
                            if last_trade:
                                minutes_since = (datetime.now() - last_trade).total_seconds() / 60
                                if minutes_since < self.trade_cooldown_minutes:
                                    continue

                        # Order side and size
                        if delta_size > 0:
                            side = "buy"
                        elif delta_size < 0:
                            side = "sell"
                        else:
                            continue
                        order_size = abs(delta_size)

                        # Position sizing constraints
                        final_size = self.pos_sizer.apply_constraints(
                            symbol=sym,
                            raw_size=order_size,
                            leverage=1.0,
                            price=px,
                            min_order_size=0.001,
                            is_closing=is_closing,
                            side=side,
                            strategy_name="enhanced_scalper"
                        )

                        if final_size > 0:
                            res = await self.order_mgr.create_order(
                                symbol=sym,
                                side=side,
                                sz=final_size,
                                px=px,
                                strategy_name="enhanced_scalper",
                                is_closing=is_closing
                            )
                            if res.success:
                                self.trade_count += 1
                                self.last_trade_time[sym] = datetime.now()
                                tag = " [CLOSE]" if is_closing else ""
                                logger.info(f"Trade #{self.trade_count}{tag}: "
                                            f"{side.upper()} {final_size:.6f} {sym} @ {px:.2f}")
                            else:
                                logger.warning(f"Order failed for {sym}: {res.error}")

                    except Exception as e:
                        logger.error(f"Execution error for {sym}: {e}")

                # 8. Periodic logging
                if self.cycle_count % 30 == 0:  # Every ~5 min at 10s interval
                    stats = self.executor.get_trade_stats()
                    logger.info(f"Cycle {self.cycle_count} | "
                                f"Trades: {stats.get('total_trades', 0)} | "
                                f"PnL: ${stats.get('total_pnl', 0):.2f} | "
                                f"Win rate: {stats.get('win_rate', 0)*100:.1f}%")

                # 9. Performance snapshot every ~10 min
                if self.cycle_count % 60 == 0:
                    try:
                        self.strategy._slog.log_performance_snapshot()
                    except Exception as e:
                        logger.debug(f"Perf snapshot error: {e}")

                # 10. Check duration
                if duration_hours:
                    elapsed = (datetime.now() - start_time).total_seconds() / 3600
                    if elapsed >= duration_hours:
                        logger.info(f"Duration reached ({duration_hours}h). Stopping.")
                        break

                await asyncio.sleep(SCALPER_CONFIG['interval_seconds'])

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Critical error: {e}", exc_info=True)
                await asyncio.sleep(10)

        # Final summary
        self._print_summary(start_time)

    def stop(self):
        self.running = False

    def _print_summary(self, start_time: datetime):
        runtime = datetime.now() - start_time
        stats = self.executor.get_trade_stats()

        logger.info("")
        logger.info("=" * 60)
        logger.info("SCALPER PAPER TRADING SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Runtime: {runtime}")
        logger.info(f"Cycles: {self.cycle_count}")
        logger.info(f"Total Trades: {stats.get('total_trades', 0)}")
        logger.info(f"Wins: {stats.get('wins', 0)} | Losses: {stats.get('losses', 0)}")
        logger.info(f"Win Rate: {stats.get('win_rate', 0)*100:.1f}%")
        logger.info(f"Total PnL: ${stats.get('total_pnl', 0):.2f}")
        logger.info(f"Return: {stats.get('return_pct', 0):.2f}%")
        logger.info("=" * 60)

        # ScalperLogger performance
        try:
            perf = self.strategy._slog.get_performance_summary()
            if perf['trade_count'] > 0:
                logger.info(f"ScalperLogger: {perf['trade_count']} trades, "
                            f"win_rate={perf['win_rate']:.1%}, "
                            f"sharpe={perf['sharpe_estimate']:.2f}, "
                            f"avg_hold={perf['avg_hold_sec']:.0f}s")
        except Exception:
            pass


async def main():
    parser = argparse.ArgumentParser(description="Enhanced Scalper Paper Trading")
    parser.add_argument("--duration", type=float, default=None,
                        help="Duration in hours (default: run until stopped)")
    parser.add_argument("--symbols", default="BTC,ETH",
                        help="Comma-separated symbols (default: BTC,ETH)")
    parser.add_argument("--equity", type=float, default=100_000,
                        help="Simulated equity (default: $100,000)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(level=args.log_level)

    # Enable WAL mode to avoid DB locking with live process
    enable_wal_mode()

    # Acquire process lock
    lock = ProcessLock(LOCK_FILE, PID_FILE)
    if not lock.acquire():
        sys.exit(1)

    runner = None
    try:
        assets = [s.strip() for s in args.symbols.split(",")]
        runner = ScalperPaperRunner(assets=assets, equity=args.equity)

        # Signal handling
        def handle_signal(sig_name):
            logger.info(f"Received {sig_name} - stopping...")
            if runner:
                runner.stop()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            sig_name = sig.name
            loop.add_signal_handler(sig, lambda s=sig_name: handle_signal(s))

        await runner.run(duration_hours=args.duration)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        lock.release()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
