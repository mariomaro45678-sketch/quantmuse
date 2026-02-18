#!/usr/bin/env python3
"""
Multi-Strategy Live Trading Runner

Runs multiple strategies simultaneously on Hyperliquid mainnet.
Each strategy operates independently with its own asset universe.

Usage:
    python scripts/run_multi_strategy.py [--duration HOURS]
    python scripts/run_multi_strategy.py --mock  # explicit paper trading only
"""

import asyncio
import argparse
import fcntl
import logging
import os
import signal
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

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
from data_service.factors.regime_detector import RegimeDetector, MarketRegime
from data_service.factors.correlation_tracker import CorrelationTracker
from data_service.strategies.ensemble_coordinator import EnsembleCoordinator
from data_service.executors.entry_timing import EntryOptimizer, EntryStrategy
from data_service.risk.dynamic_sizer import DynamicSizer
from data_service.ai.sources.economic_calendar import EconomicCalendar, get_economic_calendar
from data_service.strategies.parameter_adapter import ParameterAdapter, get_parameter_adapter

# Import strategies to trigger @register_strategy decorator
from data_service.strategies.momentum_perpetuals import MomentumPerpetuals  # noqa: F401
from data_service.strategies.mean_reversion_metals import MeanReversionMetals  # noqa: F401
from data_service.strategies.sentiment_driven import SentimentDriven  # noqa: F401
from data_service.strategies.enhanced_scalper import EnhancedScalper  # noqa: F401

logger = logging.getLogger("MultiStrategy")

# Process lock files
SCRIPT_DIR = Path(__file__).parent.parent
PID_FILE = SCRIPT_DIR / "logs" / "run_multi_strategy.pid"
LOCK_FILE = SCRIPT_DIR / "logs" / "run_multi_strategy.lock"


class ProcessLock:
    """
    File-based process singleton lock to prevent multiple instances.
    Uses fcntl.flock() for reliable cross-process locking.
    """

    def __init__(self, lock_path: Path, pid_path: Path):
        self.lock_path = lock_path
        self.pid_path = pid_path
        self._lock_fd: Optional[int] = None

    def acquire(self) -> bool:
        """
        Acquire exclusive lock. Returns False if another instance is running.
        """
        # Ensure logs directory exists
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Open lock file
            self._lock_fd = open(self.lock_path, 'w')

            # Try to acquire exclusive non-blocking lock
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Write PID to file for debugging
            self.pid_path.write_text(str(os.getpid()))

            logger.info(f"Process lock acquired (PID: {os.getpid()})")
            return True

        except BlockingIOError:
            # Another process holds the lock
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None

            # Try to read the existing PID
            existing_pid = "unknown"
            if self.pid_path.exists():
                existing_pid = self.pid_path.read_text().strip()

            logger.error(f"Another instance is already running (PID: {existing_pid})")
            return False

        except Exception as e:
            logger.error(f"Failed to acquire process lock: {e}")
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None
            return False

    def release(self):
        """Release lock and clean up files."""
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
            except Exception as e:
                logger.warning(f"Error releasing lock: {e}")
            self._lock_fd = None

        # Clean up files
        try:
            self.lock_path.unlink(missing_ok=True)
            self.pid_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Error cleaning up lock files: {e}")

        logger.info("Process lock released")


# Strategy configurations with their asset universes
STRATEGY_CONFIGS = {
    "momentum_perpetuals": {
        "enabled": True,
        "assets": ["TSLA", "NVDA", "AMD", "COIN"],  # High-volatility stocks
        "interval_seconds": 60,
        "description": "Momentum strategy for volatile stocks"
    },
    "mean_reversion_metals": {
        "enabled": True,
        "assets": ["XAU", "XAG"],  # Precious metals
        "interval_seconds": 60,
        "description": "Mean reversion on gold/silver ratio"
    },
    "sentiment_driven": {
        "enabled": True,
        "assets": ["AAPL", "GOOGL", "MSFT", "AMZN", "META"],  # Mega-cap tech
        "interval_seconds": 120,
        "description": "News sentiment-driven trading"
    },
    "enhanced_scalper": {
        "enabled": True,           # ENABLED for paper trading validation
        "paper_trading_only": True,  # SAFETY: Must run with --mock flag or will refuse to trade
        "assets": ["BTC", "ETH"],  # High liquidity crypto only
        "interval_seconds": 10,    # Fast interval for scalping
        "description": "Microstructure-based scalping (10x leverage) [PAPER ONLY]",
        # IMPORTANT: Uses separate asset universe to avoid conflicts
        # paper_trading_only=True prevents execution in live mode
    }
}


class StrategyRunner:
    """Manages a single strategy's execution loop."""

    def __init__(self, strategy_name: str, config: Dict[str, Any],
                 fetcher: HyperliquidFetcher, executor: HyperliquidExecutor,
                 risk_manager: RiskManager, db: DatabaseManager,
                 regime_detector: RegimeDetector = None,
                 correlation_tracker: CorrelationTracker = None,
                 ensemble_coordinator: EnsembleCoordinator = None,
                 entry_optimizer: EntryOptimizer = None,
                 dynamic_sizer: DynamicSizer = None,
                 economic_calendar: EconomicCalendar = None,
                 parameter_adapter: ParameterAdapter = None,
                 positions_getter: callable = None):
        self.name = strategy_name
        self.config = config
        self.assets = config["assets"]
        self.interval = config["interval_seconds"]

        self.fetcher = fetcher
        self.executor = executor
        self.risk_mgr = risk_manager
        self.db = db
        self.regime_detector = regime_detector
        self.correlation_tracker = correlation_tracker
        self.ensemble_coordinator = ensemble_coordinator
        self.entry_optimizer = entry_optimizer
        self.dynamic_sizer = dynamic_sizer
        self.economic_calendar = economic_calendar
        self.parameter_adapter = parameter_adapter
        self.positions_getter = positions_getter  # Callback to get all strategy positions

        self.order_mgr = OrderManager(executor=executor, risk_manager=risk_manager)
        self.pos_sizer = PositionSizer(risk_manager=risk_manager)

        # Entry optimization settings
        self.use_entry_optimization = config.get("use_entry_optimization", True)
        self.use_dynamic_sizing = config.get("use_dynamic_sizing", True)

        # Initialize strategy
        if strategy_name in STRATEGY_REGISTRY:
            self.strategy = STRATEGY_REGISTRY[strategy_name]()
        else:
            raise ValueError(f"Strategy {strategy_name} not found in registry")

        # Apply adaptive parameters if available
        if self.parameter_adapter:
            adapted_params = self.parameter_adapter.get_parameters(strategy_name)
            if adapted_params:
                for param_name, value in adapted_params.items():
                    if hasattr(self.strategy, param_name):
                        setattr(self.strategy, param_name, value)
                        logger.debug(f"[{strategy_name}] Adaptive param: {param_name}={value}")

        self.running = False
        self.cycle_count = 0
        self.trade_count = 0

        # Paper-trading-only safety check
        self.paper_trading_only = config.get("paper_trading_only", False)
        executor_mode = getattr(executor, 'mode', 'live')
        if self.paper_trading_only and executor_mode != 'mock':
            raise RuntimeError(
                f"[{strategy_name}] paper_trading_only=True but executor is in LIVE mode. "
                "Pass --mock flag to enable this strategy."
            )

        # Notify strategy of paper-trading mode (for ScalperLogger etc.)
        if hasattr(self.strategy, 'set_paper_trading'):
            self.strategy.set_paper_trading(executor_mode == 'mock')

        # Track last regime for logging changes
        self.last_regime: MarketRegime = None

        # Trade cooldown: per-strategy cooldowns (mean_reversion needs faster, sentiment slower)
        self.last_trade_time: Dict[str, datetime] = {}
        cooldown_defaults = {
            'momentum_perpetuals': 20,
            'mean_reversion_metals': 10,
            'sentiment_driven': 15,
            'enhanced_scalper': 2,
        }
        self.trade_cooldown_minutes = cooldown_defaults.get(strategy_name, 15)

        logger.info(f"[{self.name}] Initialized with assets: {self.assets}")

    async def run(self):
        """Main strategy execution loop."""
        self.running = True
        logger.info(f"[{self.name}] Starting execution loop")

        while self.running:
            try:
                self.cycle_count += 1
                cycle_start = datetime.now()

                # 1. Fetch market data for all assets
                market_data = {}
                for sym in self.assets:
                    try:
                        df = await self.fetcher.get_candles(sym, timeframe='1h', limit=100)
                        if not df.empty:
                            market_data[sym] = df
                            # Update reference price for slippage calculation
                            self.executor.set_price(sym, df['close'].iloc[-1])
                    except Exception as e:
                        logger.warning(f"[{self.name}] Error fetching {sym}: {e}")

                if not market_data:
                    await asyncio.sleep(10)
                    continue

                # 2. Detect market regime (if detector available)
                regime_state = None
                regime_multiplier = 1.0
                if self.regime_detector:
                    try:
                        regime_state = self.regime_detector.get_portfolio_regime(market_data)

                        # Log regime changes
                        if regime_state.regime != self.last_regime:
                            logger.info(f"[{self.name}] Regime change: {self.last_regime} -> {regime_state.regime.value} "
                                       f"(conf={regime_state.confidence:.2f}, ADX={regime_state.adx:.1f}, "
                                       f"Hurst={regime_state.hurst:.2f})")
                            self.last_regime = regime_state.regime

                        # Get strategy-specific multiplier
                        if "momentum" in self.name:
                            regime_multiplier = regime_state.momentum_multiplier
                        elif "mean_reversion" in self.name:
                            regime_multiplier = regime_state.mean_reversion_multiplier
                        else:
                            regime_multiplier = 1.0

                        # Apply position size multiplier
                        regime_multiplier *= regime_state.position_size_multiplier

                    except Exception as e:
                        logger.warning(f"[{self.name}] Regime detection error: {e}")

                # 2b. Calculate correlations (if tracker available)
                correlation_state = None
                if self.correlation_tracker:
                    try:
                        correlation_state = self.correlation_tracker.calculate(market_data)
                    except Exception as e:
                        logger.debug(f"[{self.name}] Correlation calc error: {e}")

                # 3. Calculate signals
                # Get positions from other strategies for conflict detection
                positions_by_strategy = {}
                if self.positions_getter:
                    try:
                        positions_by_strategy = self.positions_getter()
                    except Exception as e:
                        logger.debug(f"[{self.name}] Error getting cross-strategy positions: {e}")

                factors = {
                    'fetcher': self.fetcher,
                    'regime': regime_state,
                    'regime_multiplier': regime_multiplier,
                    'correlation': correlation_state,
                    'positions_by_strategy': positions_by_strategy,  # For conflict detection
                }

                # Pass positions to strategies that support conflict detection
                if hasattr(self.strategy, 'set_other_positions'):
                    self.strategy.set_other_positions(positions_by_strategy)

                try:
                    signals = await self.strategy.calculate_signals(market_data, factors)
                except Exception as e:
                    logger.error(f"[{self.name}] Signal calculation error: {e}")
                    await asyncio.sleep(10)
                    continue

                # 3b. Report signals to ensemble coordinator
                if self.ensemble_coordinator:
                    try:
                        self.ensemble_coordinator.update_signals(self.name, signals)
                    except Exception as e:
                        logger.debug(f"[{self.name}] Ensemble update error: {e}")

                # 3c. Check economic calendar for high-impact events
                calendar_multiplier = 1.0
                calendar_reason = None
                if self.economic_calendar:
                    try:
                        calendar_multiplier, calendar_reason = self.economic_calendar.get_trading_multiplier()

                        # Log significant calendar events
                        if calendar_multiplier < 1.0:
                            logger.info(f"[{self.name}] 📅 Economic event: {calendar_reason} "
                                       f"-> multiplier={calendar_multiplier:.2f}")

                        # If multiplier is 0, we're in event window - skip new entries
                        if calendar_multiplier == 0.0:
                            logger.info(f"[{self.name}] ⏸️  Event window active - skipping new entries")
                            await asyncio.sleep(60)  # Wait 1 min before next check
                            continue

                    except Exception as e:
                        logger.debug(f"[{self.name}] Economic calendar error: {e}")

                # 4. Fetch current positions AND sync to risk manager
                current_positions = {}
                try:
                    positions = await self.executor.get_positions()
                    open_positions_for_risk = []
                    for pos in positions:
                        coin = pos.symbol
                        if ':' in coin:
                            coin = coin.split(':')[1]
                        current_positions[coin] = pos.size
                        # Build position list for risk manager sync
                        open_positions_for_risk.append({
                            'symbol': coin,
                            'size': pos.size,
                            'notional': abs(pos.size * pos.entry_price),
                            'entry_price': pos.entry_price,
                        })

                    # Sync positions to risk manager so leverage checks use real data
                    self.risk_mgr.open_positions = open_positions_for_risk

                except Exception as e:
                    logger.warning(f"[{self.name}] Could not fetch positions: {e}")

                # 4b. Size positions (with dynamic sizing if available)
                raw_positions = self.strategy.size_positions(signals, None)

                # Apply calendar-based position size reduction
                if calendar_multiplier < 1.0 and calendar_multiplier > 0:
                    raw_positions = {k: v * calendar_multiplier for k, v in raw_positions.items()}

                # Apply dynamic sizing (regime + correlation aware)
                if self.dynamic_sizer and self.use_dynamic_sizing:
                    # Get current prices for sizing
                    current_prices = {}
                    for sym in raw_positions:
                        try:
                            md = await self.fetcher.get_market_data(sym)
                            current_prices[sym] = md.mid_price
                        except:
                            pass

                    # Get signal confidences
                    signal_confidences = {sym: sig.confidence for sym, sig in signals.items()}

                    # Update dynamic sizer with current positions
                    self.dynamic_sizer.update_positions(current_positions)

                    # Size portfolio with all factors
                    sizing_results = self.dynamic_sizer.size_portfolio(
                        target_positions=raw_positions,
                        current_prices=current_prices,
                        equity=self.risk_mgr.equity,
                        strategy_name=self.name,
                        regime_state=regime_state,
                        correlation_state=correlation_state,
                        signal_confidences=signal_confidences
                    )

                    # Extract adjusted sizes
                    target_positions = {sym: result.adjusted_size for sym, result in sizing_results.items()}

                    # Log significant adjustments
                    for sym, result in sizing_results.items():
                        if result.final_multiplier < 0.8 or result.final_multiplier > 1.2:
                            logger.debug(f"[{self.name}] {sym}: sized {result.raw_size:.1%} -> "
                                       f"{result.adjusted_size:.1%} ({result.rationale})")
                else:
                    # Fallback: Apply regime-based position size adjustment
                    target_positions = raw_positions
                    if regime_multiplier != 1.0:
                        target_positions = {k: v * regime_multiplier for k, v in target_positions.items()}

                # 5. Execute orders - only for CHANGES in position
                for sym, target_pct in target_positions.items():
                    try:
                        md = await self.fetcher.get_market_data(sym)
                        px = md.mid_price

                        # Calculate target size in units
                        target_size = target_pct * self.risk_mgr.equity / px

                        # Get current position size (0 if no position)
                        current_size = current_positions.get(sym, 0)

                        # Calculate the delta (what we need to trade)
                        delta_size = target_size - current_size

                        # Determine if this is a closing/reducing trade FIRST
                        # Closing = moving position toward zero (reduces risk)
                        is_closing = False
                        if current_size > 0 and delta_size < 0:  # Long position, selling
                            is_closing = True
                        elif current_size < 0 and delta_size > 0:  # Short position, buying
                            is_closing = True

                        # Skip if delta is too small
                        # Use lower threshold for closing trades ($2) vs new entries ($5)
                        delta_notional = abs(delta_size * px)
                        min_delta = 2.0 if is_closing else 5.0
                        if delta_notional < min_delta:
                            continue

                        # Log position change
                        close_tag = " [CLOSE]" if is_closing else ""
                        logger.debug(f"[{self.name}] {sym}{close_tag}: current={current_size:.4f}, "
                                   f"target={target_size:.4f}, delta={delta_size:.4f} (${delta_notional:.2f})")

                        # Check trade cooldown - NEVER apply cooldown to closing trades
                        if not is_closing:
                            last_trade = self.last_trade_time.get(sym)
                            if last_trade:
                                minutes_since = (datetime.now() - last_trade).total_seconds() / 60
                                if minutes_since < self.trade_cooldown_minutes:
                                    logger.debug(f"[{self.name}] {sym}: cooldown ({minutes_since:.0f}m < {self.trade_cooldown_minutes}m)")
                                    continue

                        # Determine order side from delta direction
                        if delta_size > 0:
                            side = "buy"
                            order_size = abs(delta_size)
                        elif delta_size < 0:
                            side = "sell"
                            order_size = abs(delta_size)
                        else:
                            continue

                        # HIP-3 minimum order value is $10
                        min_order_value = 10.0
                        notional = order_size * px
                        if notional < min_order_value:
                            if is_closing:
                                # For closing, bump to minimum - don't skip
                                order_size = min_order_value / px
                                logger.debug(f"[{self.name}] {sym}: Close bumped to ${min_order_value} min")
                            else:
                                # For new entries, check if leverage is reasonable
                                is_metal = sym in ['XAU', 'XAG', 'HG']
                                max_leverage = 5.0 if is_metal else 3.0
                                required_leverage = min_order_value / notional
                                if required_leverage > max_leverage:
                                    logger.debug(f"[{self.name}] Skipping {sym}: ${notional:.2f} needs {required_leverage:.1f}x (max {max_leverage}x)")
                                    continue
                                order_size = min_order_value / px
                                logger.info(f"[{self.name}] {sym}: Adjusted to ${min_order_value} minimum (was ${notional:.2f})")

                        # Apply position sizing constraints
                        final_size = self.pos_sizer.apply_constraints(
                            symbol=sym,
                            raw_size=order_size,
                            leverage=1.0,
                            price=px,
                            min_order_size=0.001,
                            is_closing=is_closing,
                            side=side,
                            strategy_name=self.name
                        )

                        if final_size > 0:
                            # Get signal confidence for entry optimization
                            signal_confidence = signals.get(sym).confidence if sym in signals else 0.5

                            # Use entry optimizer if available and enabled
                            # But NEVER delay closing trades - use immediate execution
                            if self.entry_optimizer and self.use_entry_optimization and not is_closing:
                                entry_result = await self.entry_optimizer.submit_entry(
                                    symbol=sym,
                                    side=side,
                                    size=final_size,
                                    current_price=px,
                                    strategy_name=self.name,
                                    signal_strength=signal_confidence
                                )
                                success = entry_result.success
                                if success:
                                    self.trade_count += 1
                                    self.last_trade_time[sym] = datetime.now()
                                    improvement = entry_result.improvement_pct
                                    logger.info(f"[{self.name}] Trade #{self.trade_count}: "
                                              f"{side.upper()} {final_size:.4f} {sym} @ {px:.2f} "
                                              f"({entry_result.entry_type}, {improvement:+.2f}% target)")
                                else:
                                    logger.warning(f"[{self.name}] Entry optimizer failed for {sym}: {entry_result.error}")
                            else:
                                # Direct order for closing trades or when optimizer disabled
                                res = await self.order_mgr.create_order(
                                    symbol=sym,
                                    side=side,
                                    sz=final_size,
                                    px=px,
                                    strategy_name=self.name,
                                    is_closing=is_closing
                                )
                                if res.success:
                                    self.trade_count += 1
                                    self.last_trade_time[sym] = datetime.now()
                                    tag = " [CLOSE]" if is_closing else ""
                                    logger.info(f"[{self.name}] Trade #{self.trade_count}{tag}: "
                                              f"{side.upper()} {final_size:.4f} {sym} @ {px:.2f}")
                                else:
                                    logger.warning(f"[{self.name}] Order failed for {sym}: {res.error}")

                    except Exception as e:
                        logger.error(f"[{self.name}] Execution error for {sym}: {e}")

                # Log cycle stats
                cycle_time = (datetime.now() - cycle_start).total_seconds()
                if self.cycle_count % 10 == 0:
                    stats = self.executor.get_trade_stats()
                    logger.info(f"[{self.name}] Cycle {self.cycle_count} | "
                              f"Trades: {stats.get('total_trades', 0)} | "
                              f"PnL: ${stats.get('total_pnl', 0):.2f} | "
                              f"Cycle time: {cycle_time:.1f}s")

                    # Sync orders with exchange every 10 cycles
                    try:
                        await self.order_mgr.sync_orders()
                    except Exception as e:
                        logger.warning(f"[{self.name}] Order sync error: {e}")

                # Scalper-specific: log performance snapshot every 60 cycles (~10 min at 10s)
                if (self.name == 'enhanced_scalper'
                        and self.cycle_count % 60 == 0
                        and hasattr(self.strategy, '_slog')):
                    try:
                        self.strategy._slog.log_performance_snapshot()
                    except Exception as e:
                        logger.debug(f"[{self.name}] Perf snapshot error: {e}")

                await asyncio.sleep(self.interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.name}] Critical error: {e}", exc_info=True)
                await asyncio.sleep(10)

        logger.info(f"[{self.name}] Stopped after {self.cycle_count} cycles, {self.trade_count} trades")

    def stop(self):
        self.running = False


class MultiStrategyManager:
    """Coordinates multiple strategy runners with centralized position tracking."""

    def __init__(self, mode: str = "mock", equity: float = 100_000):
        self.runners: Dict[str, StrategyRunner] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.mode = mode
        self.equity = equity

        # Shared components
        self.db = DatabaseManager()
        self.fetcher = HyperliquidFetcher(mode=mode)
        self.executor = HyperliquidExecutor(mode=mode)
        self.risk_mgr = RiskManager(db_manager=self.db)
        self.regime_detector = RegimeDetector()
        self.correlation_tracker = CorrelationTracker()
        self.ensemble_coordinator = EnsembleCoordinator()
        self.economic_calendar = get_economic_calendar()
        self.parameter_adapter = get_parameter_adapter()

        # Entry timing optimizer (shared across strategies)
        self.entry_optimizer = EntryOptimizer(
            order_manager=OrderManager(executor=self.executor, risk_manager=self.risk_mgr),
            price_fetcher=self._fetch_price
        )

        # Dynamic position sizer (shared across strategies)
        self.dynamic_sizer = DynamicSizer(
            risk_manager=self.risk_mgr,
            correlation_tracker=self.correlation_tracker
        )

        # Set initial equity (from live account or default)
        self.risk_mgr.set_portfolio(equity=equity, open_positions=[])

        # Centralized position tracking across all strategies
        self.positions_by_strategy: Dict[str, Dict[str, float]] = {}
        self.total_positions: Dict[str, float] = {}

        # Register trade callback for logging
        self.executor.register_trade_callback(self._on_trade)

        self.start_time = None
        self.running = False
        self._monitor_cycle = 0  # For hourly parameter adaptation

    async def _fetch_price(self, symbol: str) -> float:
        """Fetch current price for a symbol (used by entry optimizer)."""
        try:
            md = await self.fetcher.get_market_data(symbol)
            return md.mid_price
        except Exception:
            return None

    def _on_trade(self, trade):
        """Callback for trade notifications - updates centralized position tracking."""
        # Update position tracking
        strategy = trade.strategy
        symbol = trade.symbol
        size_delta = trade.size if trade.side == "buy" else -trade.size

        if strategy not in self.positions_by_strategy:
            self.positions_by_strategy[strategy] = {}

        current = self.positions_by_strategy[strategy].get(symbol, 0.0)
        new_pos = current + size_delta
        if abs(new_pos) < 1e-8:
            self.positions_by_strategy[strategy].pop(symbol, None)
        else:
            self.positions_by_strategy[strategy][symbol] = new_pos

        # Update total positions
        self.total_positions[symbol] = sum(
            strat_pos.get(symbol, 0.0)
            for strat_pos in self.positions_by_strategy.values()
        )

        # Log the trade with position context
        total_pos = self.total_positions.get(symbol, 0.0)
        logger.info(f"📈 TRADE: {trade.side.upper()} {trade.size:.4f} {symbol} "
                   f"@ ${trade.fill_price:.2f} | PnL: ${trade.pnl:.2f} | "
                   f"Strategy: {strategy} | Total {symbol} pos: {total_pos:+.4f}")

        # Record parameter snapshot for adaptive tuning
        if self.parameter_adapter and hasattr(trade, 'order_id') and trade.order_id:
            try:
                self.parameter_adapter.record_trade_parameters(
                    trade_id=trade.order_id,
                    strategy_name=strategy,
                    timestamp=datetime.now()
                )
            except Exception as e:
                logger.debug(f"Parameter snapshot error: {e}")

    def get_all_positions(self) -> Dict[str, Dict[str, Any]]:
        """Get all positions across all strategies."""
        result = {}
        for symbol, total_size in self.total_positions.items():
            if abs(total_size) > 1e-8:
                by_strategy = {}
                for strat, positions in self.positions_by_strategy.items():
                    if symbol in positions:
                        by_strategy[strat] = positions[symbol]
                result[symbol] = {
                    'total_size': total_size,
                    'by_strategy': by_strategy
                }
        return result

    def log_position_summary(self):
        """Log a summary of all positions across strategies."""
        positions = self.get_all_positions()
        if not positions:
            logger.info("📊 No open positions across any strategy")
            return

        logger.info("=" * 50)
        logger.info("📊 POSITION SUMMARY (All Strategies)")
        logger.info("=" * 50)
        for symbol, data in positions.items():
            direction = "LONG" if data['total_size'] > 0 else "SHORT"
            logger.info(f"  {symbol}: {direction} {abs(data['total_size']):.4f}")
            for strat, sz in data['by_strategy'].items():
                logger.info(f"    └─ {strat}: {sz:+.4f}")
        logger.info("=" * 50)

    def initialize_strategies(self):
        """Initialize all enabled strategies."""
        for name, config in STRATEGY_CONFIGS.items():
            if config.get("enabled", False):
                try:
                    runner = StrategyRunner(
                        strategy_name=name,
                        config=config,
                        fetcher=self.fetcher,
                        executor=self.executor,
                        risk_manager=self.risk_mgr,
                        db=self.db,
                        regime_detector=self.regime_detector,
                        correlation_tracker=self.correlation_tracker,
                        ensemble_coordinator=self.ensemble_coordinator,
                        entry_optimizer=self.entry_optimizer,
                        dynamic_sizer=self.dynamic_sizer,
                        economic_calendar=self.economic_calendar,
                        parameter_adapter=self.parameter_adapter,
                        positions_getter=lambda: self.positions_by_strategy  # For conflict detection
                    )
                    self.runners[name] = runner
                    logger.info(f"Initialized strategy: {name} - {config['description']}")
                except Exception as e:
                    logger.error(f"Failed to initialize {name}: {e}")

    async def _position_monitor(self):
        """Periodic position monitoring, equity refresh, and summary logging."""
        while self.running:
            await asyncio.sleep(300)  # Every 5 minutes
            if self.running:
                try:
                    # === REFRESH EQUITY FROM EXCHANGE ===
                    try:
                        user_state = await self.executor.get_user_state()
                        if user_state.equity > 0:
                            old_equity = self.equity
                            self.equity = user_state.equity
                            self.risk_mgr.equity = user_state.equity
                            self.risk_mgr.session_high_equity = max(
                                self.risk_mgr.session_high_equity, user_state.equity
                            )
                            if abs(old_equity - user_state.equity) > 0.01:
                                logger.info(f"Equity refreshed: ${old_equity:.2f} -> ${user_state.equity:.2f}")
                    except Exception as e:
                        logger.debug(f"Equity refresh error: {e}")

                    # Fetch actual positions from exchange
                    positions = await self.executor.get_positions()

                    # === SYNC POSITIONS TO RISK MANAGER ===
                    open_positions_for_risk = []
                    for pos in positions:
                        symbol = pos.symbol.split(':')[-1] if ':' in pos.symbol else pos.symbol
                        open_positions_for_risk.append({
                            'symbol': symbol,
                            'size': pos.size,
                            'notional': abs(pos.size * pos.entry_price),
                            'entry_price': pos.entry_price,
                        })
                    self.risk_mgr.open_positions = open_positions_for_risk

                    # Log position summary
                    logger.info("=" * 50)
                    logger.info(f"📊 POSITION CHECK | Equity: ${self.equity:.2f}")
                    logger.info("=" * 50)

                    total_notional = 0.0
                    position_pcts = {}
                    for pos in positions:
                        # Extract symbol without DEX prefix
                        symbol = pos.symbol.split(':')[-1] if ':' in pos.symbol else pos.symbol
                        direction = "LONG" if pos.size > 0 else "SHORT"
                        notional = abs(pos.size * pos.entry_price)
                        total_notional += notional
                        position_pcts[symbol] = notional / self.equity if self.equity > 0 else 0
                        logger.info(f"  {symbol}: {direction} {abs(pos.size):.4f} @ ${pos.entry_price:.2f} "
                                   f"(${notional:.2f}) | uPnL: ${pos.unrealized_pnl:.2f}")

                    if not positions:
                        logger.info("  No open positions")
                    else:
                        exposure_pct = total_notional / self.equity * 100 if self.equity > 0 else 0
                        logger.info(f"  Total exposure: ${total_notional:.2f} ({exposure_pct:.1f}% of equity)")

                        # Calculate effective exposure using correlation tracker
                        if position_pcts:
                            try:
                                corr_state = self.correlation_tracker._cache
                                if corr_state:
                                    effective_exp = self.correlation_tracker.get_effective_exposure(
                                        position_pcts, corr_state
                                    )
                                    eff_pct = effective_exp["effective_exposure"] * 100
                                    div_score = effective_exp.get("diversification_score", 1.0)
                                    logger.info(f"  Effective exposure: {eff_pct:.1f}% (diversification: {div_score:.2f})")

                                    # Warn on high correlation pairs
                                    if corr_state.high_correlation_pairs:
                                        pairs_str = ", ".join([str(p) for p in corr_state.high_correlation_pairs[:3]])
                                        logger.info(f"  High corr pairs: {pairs_str}")
                            except Exception as e:
                                logger.debug(f"Correlation calc error: {e}")

                    # Log ensemble sentiment
                    try:
                        sentiment = self.ensemble_coordinator.get_portfolio_sentiment()
                        logger.info(f"  Ensemble: {sentiment.sentiment.upper()} "
                                   f"(L:{sentiment.long_count} S:{sentiment.short_count} F:{sentiment.flat_count}) "
                                   f"Avg conf: {sentiment.avg_confidence:.2f}")
                    except Exception as e:
                        logger.debug(f"Ensemble sentiment error: {e}")

                    logger.info("=" * 50)

                    # Run parameter adaptation hourly (every 12 cycles of 5min = 60min)
                    self._monitor_cycle += 1
                    if self.parameter_adapter and self._monitor_cycle % 12 == 0:
                        try:
                            for strategy_name in self.runners.keys():
                                results = self.parameter_adapter.adapt_parameters(strategy_name)
                                if results:
                                    logger.info(f"🔧 Parameter adaptation for {strategy_name}: "
                                               f"{len(results)} changes")
                        except Exception as e:
                            logger.debug(f"Parameter adaptation error: {e}")

                except Exception as e:
                    logger.warning(f"Position monitor error: {e}")

    async def run(self, duration_hours: float = None):
        """Run all strategies concurrently with position monitoring."""
        self.running = True
        self.start_time = datetime.now()

        logger.info("=" * 60)
        mode_label = "MOCK (paper)" if self.mode == "mock" else "LIVE"
        logger.info(f"MULTI-STRATEGY {mode_label} TRADING STARTED")
        logger.info(f"Strategies: {list(self.runners.keys())}")
        logger.info(f"Duration: {duration_hours}h" if duration_hours else "Duration: Unlimited")
        logger.info(f"Initial Equity: ${self.risk_mgr.equity:.2f}")
        logger.info(f"Regime Detection: ENABLED")
        logger.info(f"Correlation Tracking: ENABLED")
        logger.info(f"Ensemble Voting: ENABLED")
        logger.info(f"Entry Timing: ENABLED")
        logger.info(f"Dynamic Sizing: ENABLED")
        logger.info(f"Economic Calendar: ENABLED")
        logger.info(f"Adaptive Parameters: ENABLED")

        # Log upcoming economic events
        try:
            self.economic_calendar.refresh(force=False)
            upcoming = self.economic_calendar.get_upcoming_events(hours_ahead=24)
            if upcoming:
                logger.info(f"📅 Upcoming events (24h): {len(upcoming)}")
                for event in upcoming[:3]:
                    logger.info(f"   {event.datetime_utc.strftime('%H:%M')} UTC | "
                               f"{event.impact.value.upper():8} | {event.event_name}")
        except Exception as e:
            logger.warning(f"Could not load economic calendar: {e}")

        logger.info("=" * 60)

        # Start entry optimizer monitor
        await self.entry_optimizer.start()

        # Start all strategy tasks
        for name, runner in self.runners.items():
            task = asyncio.create_task(runner.run())
            self.tasks[name] = task

        # Start position monitor
        monitor_task = asyncio.create_task(self._position_monitor())

        # Wait for duration or until stopped
        try:
            if duration_hours:
                await asyncio.sleep(duration_hours * 3600)
                self.stop_all()
            else:
                # Run until cancelled
                await asyncio.gather(*self.tasks.values())
        except asyncio.CancelledError:
            pass

        # Cancel monitor
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

        # Wait for all tasks to complete
        for task in self.tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Stop entry optimizer
        await self.entry_optimizer.stop()

        # Print final summary
        self._print_summary()

    def stop_all(self):
        """Stop all running strategies."""
        self.running = False
        for runner in self.runners.values():
            runner.stop()
        # Entry optimizer will be stopped in run_strategies cleanup

    async def graceful_shutdown(self, timeout: float = 30.0):
        """
        Graceful shutdown: cancel all pending orders and close all positions.

        Args:
            timeout: Maximum time (seconds) to wait for shutdown operations
        """
        logger.info("=" * 60)
        logger.info("GRACEFUL SHUTDOWN INITIATED")
        logger.info("=" * 60)

        try:
            # Wrap shutdown operations in timeout
            await asyncio.wait_for(
                self._shutdown_operations(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"Graceful shutdown timed out after {timeout}s")
        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")

        logger.info("Graceful shutdown complete")

    async def _shutdown_operations(self):
        """Internal shutdown operations: cancel orders, close positions."""
        # 1. Cancel all pending orders
        logger.info("Step 1: Cancelling pending orders...")
        try:
            open_orders = await self.executor.get_open_orders()
            if open_orders:
                logger.info(f"Found {len(open_orders)} open orders to cancel")
                for order in open_orders:
                    try:
                        # Extract symbol without DEX prefix if present
                        symbol = order.symbol.split(':')[-1] if ':' in order.symbol else order.symbol
                        await self.executor.cancel_order(symbol, order.order_id)
                        logger.info(f"  Cancelled: {order.order_id} ({symbol} {order.side} {order.size})")
                    except Exception as e:
                        logger.error(f"  Failed to cancel order {order.order_id}: {e}")
            else:
                logger.info("  No open orders to cancel")
        except Exception as e:
            logger.error(f"  Error fetching open orders: {e}")

        # 2. Close all open positions at market
        logger.info("Step 2: Closing open positions...")
        try:
            positions = await self.executor.get_positions()
            open_positions = [p for p in positions if abs(p.size) > 1e-8]

            if open_positions:
                logger.info(f"Found {len(open_positions)} positions to close")
                for pos in open_positions:
                    try:
                        # Determine close side
                        side = "sell" if pos.size > 0 else "buy"
                        size = abs(pos.size)

                        # Extract symbol without DEX prefix
                        symbol = pos.symbol.split(':')[-1] if ':' in pos.symbol else pos.symbol

                        # Place market order to close
                        result = await self.executor.place_order(
                            symbol=symbol,
                            side=side,
                            sz=size,
                            px=None,  # Market order
                            strategy="graceful_shutdown"
                        )

                        if result.success:
                            logger.info(f"  Closed: {side.upper()} {size:.4f} {symbol}")
                        else:
                            logger.error(f"  Failed to close {symbol}: {result.error}")
                    except Exception as e:
                        logger.error(f"  Error closing position {pos.symbol}: {e}")
            else:
                logger.info("  No open positions to close")
        except Exception as e:
            logger.error(f"  Error fetching positions: {e}")

    def _print_summary(self):
        """Print final trading summary."""
        runtime = datetime.now() - self.start_time if self.start_time else timedelta(0)
        stats = self.executor.get_trade_stats()

        logger.info("\n" + "=" * 60)
        logger.info("MULTI-STRATEGY TRADING SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Runtime: {runtime}")
        logger.info(f"Total Trades: {stats.get('total_trades', 0)}")
        logger.info(f"Wins: {stats.get('wins', 0)} | Losses: {stats.get('losses', 0)}")
        logger.info(f"Win Rate: {stats.get('win_rate', 0)*100:.1f}%")
        logger.info(f"Total PnL: ${stats.get('total_pnl', 0):.2f}")
        logger.info(f"Total Fees: ${stats.get('total_fees', 0):.2f}")
        logger.info(f"Avg Slippage: {stats.get('avg_slippage_bps', 0):.1f} bps")

        # Entry optimization stats
        entry_stats = self.entry_optimizer.get_stats()
        if entry_stats.get('total_entries', 0) > 0:
            logger.info("-" * 40)
            logger.info("Entry Timing Stats:")
            logger.info(f"  Total Entries: {entry_stats['total_entries']}")
            logger.info(f"  Chase Rate: {entry_stats['chase_rate']*100:.1f}%")
            logger.info(f"  Avg Improvement: {entry_stats['avg_improvement_bps']:.1f} bps")
            logger.info(f"  Avg Wait: {entry_stats['avg_wait_seconds']:.0f}s")
        logger.info(f"Return: {stats.get('return_pct', 0):.2f}%")
        logger.info("=" * 60)

        # Per-strategy breakdown
        logger.info("\nPer-Strategy Breakdown:")
        for name, runner in self.runners.items():
            logger.info(f"  {name}: {runner.trade_count} trades, {runner.cycle_count} cycles")

        # Final position summary
        self.log_position_summary()


async def get_live_balance(wallet_address: str) -> float:
    """Fetch live balance from Hyperliquid mainnet."""
    import aiohttp
    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "clearinghouseState", "user": wallet_address}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                margin = data.get("marginSummary", {})
                return float(margin.get("accountValue", 0))
            return 0


async def main():
    parser = argparse.ArgumentParser(description="Multi-Strategy Trading")
    parser.add_argument("--duration", type=float, default=None,
                       help="Duration in hours (default: run until stopped)")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--mock", action="store_true",
                       help="Run in MOCK mode with paper money (for testing only)")
    parser.add_argument("--no-graceful-shutdown", action="store_true",
                       help="Skip graceful shutdown (don't close positions on exit)")
    args = parser.parse_args()

    setup_logging(level=args.log_level)

    # === PHASE 1: Acquire process lock ===
    lock = ProcessLock(LOCK_FILE, PID_FILE)
    if not lock.acquire():
        logger.error("Cannot start: another instance is already running")
        logger.error(f"If this is incorrect, delete: {LOCK_FILE}")
        sys.exit(1)

    manager = None
    shutdown_triggered = False

    try:
        mode = "mock" if args.mock else "live"
        equity = 100_000  # Default for mock

        if args.mock:
            logger.warning("=" * 60)
            logger.warning("  MOCK MODE - PAPER TRADING ONLY, NO REAL ORDERS")
            logger.warning("=" * 60)
        else:
            logger.warning("=" * 60)
            logger.warning("  LIVE TRADING MODE - REAL MONEY")
            logger.warning("=" * 60)

            # Fetch live balance
            config = get_config()
            wallet = config.hyperliquid.get("wallet_address")
            if wallet:
                equity = await get_live_balance(wallet)
                logger.info(f"Live account equity: ${equity:.2f}")
            else:
                logger.error("No wallet address configured for live trading")
                return

        manager = MultiStrategyManager(mode=mode, equity=equity)
        manager.initialize_strategies()

        # === PHASE 2: Signal handling with graceful shutdown ===
        shutdown_event = asyncio.Event()

        def handle_shutdown_signal(sig_name: str):
            nonlocal shutdown_triggered
            if shutdown_triggered:
                logger.warning(f"Received {sig_name} again - forcing exit")
                sys.exit(1)
            shutdown_triggered = True
            logger.info(f"Received {sig_name} - initiating shutdown...")
            manager.stop_all()
            shutdown_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            sig_name = sig.name
            loop.add_signal_handler(sig, lambda s=sig_name: handle_shutdown_signal(s))

        # Run strategies
        await manager.run(duration_hours=args.duration)

        # Graceful shutdown (if not disabled and not in mock mode)
        if not args.no_graceful_shutdown and mode == "live":
            await manager.graceful_shutdown(timeout=30.0)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        # Always release the lock
        lock.release()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
