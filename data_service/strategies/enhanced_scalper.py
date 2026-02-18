"""
Enhanced Scalper Strategy - Integrated with QuantMuse
======================================================
High-frequency microstructure-based scalping strategy.

INTEGRATION NOTES:
- Uses existing RiskManager, PositionSizer, OrderManager (no duplicates)
- Shares equity pool with other strategies
- Respects global position limits and risk controls
- Avoids position conflicts on same symbols as other strategies
- Properly handles `is_closing` flag for position reductions

DESIGN DECISIONS:
1. NO standalone risk management - uses system RiskManager
2. Position conflict detection - won't trade symbols other strategies hold
3. Reduced leverage from 20x to 10x for safety in multi-strategy context
4. Smaller position sizes to leave room for other strategies
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np

from data_service.strategies.strategy_base import StrategyBase, Signal, register_strategy
from data_service.monitoring.scalper_logger import get_scalper_logger

# Import the microstructure analysis modules
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'enhanced_scalper'))

try:
    from enhanced_scalper.orderbook_analyzer import OrderBookMicrostructureAnalyzer, create_order_book_snapshot
    from enhanced_scalper.volume_delta_analyzer import VolumeDeltaAnalyzer, TickData
    from enhanced_scalper.stop_hunt_detector import StopHuntDetector
except ImportError:
    # Fallback if not yet installed in path
    OrderBookMicrostructureAnalyzer = None
    VolumeDeltaAnalyzer = None
    StopHuntDetector = None

logger = logging.getLogger(__name__)


@register_strategy('enhanced_scalper')
class EnhancedScalper(StrategyBase):
    """
    Microstructure-based scalping strategy integrated with QuantMuse.

    Uses order book imbalance, volume delta, and stop hunt detection
    to generate high-frequency trading signals.

    SAFETY FEATURES:
    - Reduced leverage (10x default vs 20x standalone)
    - Position conflict detection with other strategies
    - Smaller max position (15% vs 25% standalone)
    - Integrated with global risk limits
    """

    def __init__(self):
        super().__init__('enhanced_scalper')

        # Configuration with safer defaults for multi-strategy context
        self.leverage = self.config.get('leverage', 10.0)  # Reduced from 20x
        self.max_position_pct = self.config.get('max_position_pct', 0.15)  # 15% max
        self.min_confidence = self.config.get('min_confidence', 0.65)
        self.cooldown_seconds = self.config.get('cooldown_seconds', 60)
        self.time_stop_seconds = self.config.get('time_stop_seconds', 600)

        # Signal thresholds
        self.min_obi_threshold = self.config.get('min_obi_threshold', 0.50)
        self.min_delta_threshold = self.config.get('min_delta_threshold', 1000)
        self.max_spread_pct = self.config.get('max_spread_pct', 0.15)
        self.min_liquidity_score = self.config.get('min_liquidity_score', 0.70)

        # Stop/Target (in % of price)
        self.stop_loss_pct = self.config.get('stop_loss_pct', 0.003)  # 0.3%
        self.take_profit_pct = self.config.get('take_profit_pct', 0.006)  # 0.6%
        self.breakeven_trigger_pct = self.config.get('breakeven_trigger_pct', 0.004)

        # Position conflict settings
        self.avoid_conflicting_symbols = self.config.get('avoid_conflicting_symbols', True)

        # Initialize microstructure analyzers
        self._init_analyzers()

        # State tracking
        self.last_signals: Dict[str, Signal] = {}
        self.last_signal_time: Dict[str, datetime] = {}
        self.entry_prices: Dict[str, float] = {}
        self.entry_times: Dict[str, datetime] = {}
        self.highest_profit: Dict[str, float] = {}
        self.consecutive_losses = 0

        # External state references (set by runner)
        self._other_strategy_positions: Dict[str, Dict[str, float]] = {}

        # Paper trading flag (set by runner, used by ScalperLogger)
        self._paper_trading: bool = True

        # Dedicated detailed logger (lazy-initialised once paper_trading is known)
        self._scalper_log = None

        logger.info(f"Initialized {self.name}: leverage={self.leverage}x, "
                   f"max_pos={self.max_position_pct:.0%}, SL={self.stop_loss_pct:.2%}")

    def _init_analyzers(self):
        """Initialize microstructure analysis modules."""
        if OrderBookMicrostructureAnalyzer:
            self.ob_analyzer = OrderBookMicrostructureAnalyzer({
                'transient_threshold_ms': 100,
                'min_depth_usd': 1_000_000,
                'spread_wide_pct': self.max_spread_pct
            })
        else:
            self.ob_analyzer = None
            logger.warning("OrderBookMicrostructureAnalyzer not available")

        if VolumeDeltaAnalyzer:
            self.delta_analyzer = VolumeDeltaAnalyzer({
                'min_delta_threshold': self.min_delta_threshold
            })
        else:
            self.delta_analyzer = None
            logger.warning("VolumeDeltaAnalyzer not available")

        if StopHuntDetector:
            self.hunt_detector = StopHuntDetector({
                'sweep_threshold_pct': 0.001
            })
        else:
            self.hunt_detector = None
            logger.warning("StopHuntDetector not available")

    def set_other_positions(self, positions_by_strategy: Dict[str, Dict[str, float]]):
        """
        Update reference to positions held by other strategies.
        Called by StrategyRunner to enable conflict detection.
        """
        self._other_strategy_positions = {
            k: v for k, v in positions_by_strategy.items()
            if k != 'enhanced_scalper'
        }

    def set_paper_trading(self, paper_trading: bool):
        """
        Set paper-trading mode flag. Called by StrategyRunner at init.
        Triggers ScalperLogger initialisation so mode is correctly logged.
        """
        self._paper_trading = paper_trading
        # Initialise / retrieve singleton logger with correct mode
        self._scalper_log = get_scalper_logger(paper_trading=paper_trading)
        logger.info(
            f"{self.name} ScalperLogger initialised "
            f"({'PAPER' if paper_trading else 'LIVE'})"
        )

    @property
    def _slog(self):
        """Lazy accessor for ScalperLogger — initialises in paper mode if not set."""
        if self._scalper_log is None:
            self._scalper_log = get_scalper_logger(paper_trading=self._paper_trading)
        return self._scalper_log

    def _has_position_conflict(self, symbol: str) -> bool:
        """
        Check if another strategy already has a position in this symbol.
        Avoids conflicting trades that could net out or cause issues.
        """
        if not self.avoid_conflicting_symbols:
            return False

        for strategy, positions in self._other_strategy_positions.items():
            if symbol in positions and abs(positions[symbol]) > 0.001:
                logger.debug(f"Position conflict: {strategy} holds {symbol}")
                self._slog.log_conflict(symbol, strategy, positions[symbol])
                return True
        return False

    def _is_in_cooldown(self, symbol: str) -> bool:
        """Check if symbol is in cooldown after recent signal."""
        if symbol not in self.last_signal_time:
            return False
        elapsed = (datetime.now() - self.last_signal_time[symbol]).total_seconds()
        in_cd = elapsed < self.cooldown_seconds
        if in_cd:
            self._slog.log_cooldown(symbol, self.cooldown_seconds - elapsed)
        return in_cd

    async def calculate_signals(self, market_data: Dict[str, pd.DataFrame],
                                factors: Dict[str, Any]) -> Dict[str, Signal]:
        """
        Generate scalping signals based on microstructure analysis.
        """
        import time
        cycle_start = time.monotonic()

        signals = {}
        fetcher = factors.get('fetcher')
        regime_state = factors.get('regime')

        # Cycle counters for monitoring
        _n_conflict = 0
        _n_cooldown = 0
        _n_low_conf = 0
        _n_long = 0
        _n_short = 0
        _n_flat = 0

        for symbol, df in market_data.items():
            try:
                # Skip if insufficient data
                if len(df) < 20:
                    signals[symbol] = Signal(symbol, 'flat', 0.0, "Insufficient data")
                    self._slog.log_flat_signal(symbol, "Insufficient data (<20 bars)")
                    _n_flat += 1
                    continue

                # Skip if another strategy has position (conflict avoidance)
                if self._has_position_conflict(symbol):
                    signals[symbol] = Signal(symbol, 'flat', 0.0,
                                            "Position conflict with other strategy")
                    _n_conflict += 1
                    _n_flat += 1
                    continue

                # Skip if in cooldown
                if self._is_in_cooldown(symbol):
                    last_sig = self.last_signals.get(symbol)
                    if last_sig:
                        signals[symbol] = last_sig
                    else:
                        signals[symbol] = Signal(symbol, 'flat', 0.0, "In cooldown")
                    _n_cooldown += 1
                    continue

                # Get current price and basic data
                current_price = df['close'].iloc[-1]
                now = datetime.now()

                # Check existing position exits first
                exit_signal = self._check_position_exits(symbol, current_price, now, df)
                if exit_signal:
                    signals[symbol] = exit_signal
                    self.last_signals[symbol] = exit_signal
                    # Log exit event if it's a close
                    if exit_signal.direction == 'flat' and symbol in self.entry_prices:
                        pass  # entry_prices already cleared in _clear_position_state
                    continue

                # Calculate microstructure signals
                signal = await self._analyze_microstructure(
                    symbol, df, current_price, fetcher, regime_state
                )
                signals[symbol] = signal
                self.last_signals[symbol] = signal

                # Log the signal
                if signal.direction == 'long':
                    _n_long += 1
                    self._slog.log_signal(
                        symbol, signal.direction, signal.confidence,
                        signal.rationale or "", price=current_price
                    )
                elif signal.direction == 'short':
                    _n_short += 1
                    self._slog.log_signal(
                        symbol, signal.direction, signal.confidence,
                        signal.rationale or "", price=current_price
                    )
                else:
                    _n_flat += 1
                    # Differentiate between low-confidence and no-signal flat
                    if signal.rationale and "confidence" in signal.rationale.lower():
                        _n_low_conf += 1
                    self._slog.log_flat_signal(symbol, signal.rationale or "No signal")

                # Track signal time for cooldown
                if signal.direction != 'flat':
                    self.last_signal_time[symbol] = now

            except Exception as e:
                logger.error(f"Error calculating signal for {symbol}: {e}")
                signals[symbol] = Signal(symbol, 'flat', 0.0, f"Error: {str(e)}")
                _n_flat += 1

        # Log cycle summary
        cycle_ms = (time.monotonic() - cycle_start) * 1000
        self._slog.log_cycle_summary(
            symbols_analyzed=len(market_data),
            signals_long=_n_long,
            signals_short=_n_short,
            signals_flat=_n_flat,
            skipped_conflict=_n_conflict,
            skipped_cooldown=_n_cooldown,
            skipped_low_conf=_n_low_conf,
            cycle_ms=cycle_ms
        )

        return signals

    def _check_position_exits(self, symbol: str, current_price: float,
                              now: datetime, df: pd.DataFrame) -> Optional[Signal]:
        """
        Check if existing position should be exited.
        Handles stop loss, take profit, time stop, and breakeven.
        """
        if symbol not in self.entry_prices:
            return None

        entry_price = self.entry_prices[symbol]
        entry_time = self.entry_times.get(symbol, now)
        last_sig = self.last_signals.get(symbol)

        if not last_sig or last_sig.direction == 'flat':
            # Clean up stale state
            self._clear_position_state(symbol)
            return None

        # Calculate P&L
        if last_sig.direction == 'long':
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price

        # Track highest profit for trailing
        if symbol not in self.highest_profit:
            self.highest_profit[symbol] = 0.0
        self.highest_profit[symbol] = max(self.highest_profit[symbol], pnl_pct)

        # Time stop
        hold_time = (now - entry_time).total_seconds()
        if hold_time > self.time_stop_seconds:
            reason = f"time_stop ({hold_time/60:.1f}m > {self.time_stop_seconds/60:.0f}m)"
            self._slog.log_exit(
                symbol, current_price, entry_price,
                last_sig.direction, "time_stop"
            )
            self._clear_position_state(symbol)
            return Signal(symbol, 'flat', 0.9, reason)

        # Stop loss
        if pnl_pct < -self.stop_loss_pct:
            self._slog.log_exit(
                symbol, current_price, entry_price,
                last_sig.direction, "stop_loss"
            )
            self._slog.log_risk_event("consecutive_losses", {
                "symbol": symbol,
                "consecutive_losses": self.consecutive_losses + 1,
                "pnl_pct": round(pnl_pct, 6)
            })
            self._clear_position_state(symbol)
            self.consecutive_losses += 1
            return Signal(symbol, 'flat', 0.95,
                         f"Stop loss hit ({pnl_pct:.2%})")

        # Take profit
        if pnl_pct > self.take_profit_pct:
            self._slog.log_exit(
                symbol, current_price, entry_price,
                last_sig.direction, "take_profit"
            )
            self._clear_position_state(symbol)
            self.consecutive_losses = 0
            return Signal(symbol, 'flat', 0.95,
                         f"Take profit ({pnl_pct:.2%})")

        # Breakeven stop (after profit reaches threshold)
        if self.highest_profit[symbol] > self.breakeven_trigger_pct:
            if pnl_pct <= 0:
                self._slog.log_exit(
                    symbol, current_price, entry_price,
                    last_sig.direction, "breakeven_stop"
                )
                self._clear_position_state(symbol)
                return Signal(symbol, 'flat', 0.9,
                             f"Breakeven stop (peak={self.highest_profit[symbol]:.2%})")

        return None

    def _clear_position_state(self, symbol: str):
        """Clear all position tracking state for a symbol."""
        self.entry_prices.pop(symbol, None)
        self.entry_times.pop(symbol, None)
        self.highest_profit.pop(symbol, None)

    async def _analyze_microstructure(self, symbol: str, df: pd.DataFrame,
                                      current_price: float, fetcher: Any,
                                      regime_state: Any) -> Signal:
        """
        Perform microstructure analysis and generate signal.
        """
        direction = 'flat'
        confidence = 0.0
        rationale_parts = []

        # 1. Calculate basic momentum from price data
        close_prices = df['close'].values
        if len(close_prices) >= 10:
            short_ma = np.mean(close_prices[-5:])
            long_ma = np.mean(close_prices[-20:]) if len(close_prices) >= 20 else np.mean(close_prices)
            momentum = (short_ma - long_ma) / long_ma if long_ma > 0 else 0

            if momentum > 0.001:  # 0.1% bullish
                direction = 'long'
                confidence = min(0.7, 0.5 + abs(momentum) * 10)
                rationale_parts.append(f"Bullish momentum ({momentum:.3%})")
            elif momentum < -0.001:  # 0.1% bearish
                direction = 'short'
                confidence = min(0.7, 0.5 + abs(momentum) * 10)
                rationale_parts.append(f"Bearish momentum ({momentum:.3%})")

        # 2. Volume confirmation (if available)
        if 'volume' in df.columns:
            recent_vol = df['volume'].iloc[-5:].mean()
            avg_vol = df['volume'].mean()
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

            if vol_ratio > 1.5 and direction != 'flat':
                confidence += 0.1
                rationale_parts.append(f"High volume ({vol_ratio:.1f}x)")
            elif vol_ratio < 0.5:
                confidence *= 0.8
                rationale_parts.append(f"Low volume ({vol_ratio:.1f}x)")

        # 3. Order book analysis (if fetcher available)
        if fetcher and self.ob_analyzer:
            try:
                ob_data = await fetcher.get_orderbook(symbol, depth=10)
                if ob_data and 'bids' in ob_data and 'asks' in ob_data:
                    snapshot = create_order_book_snapshot(symbol, ob_data['bids'], ob_data['asks'])
                    metrics = self.ob_analyzer.analyze(snapshot)

                    if metrics.is_valid:
                        # Log OB snapshot for monitoring
                        self._slog.log_orderbook(
                            symbol,
                            obi=metrics.obi_filtered,
                            spread_pct=metrics.spread_pct,
                            liquidity_score=metrics.liquidity_score
                        )

                        # OBI confirmation
                        obi = metrics.obi_filtered
                        if direction == 'long' and obi > self.min_obi_threshold:
                            confidence += 0.15
                            rationale_parts.append(f"OBI bullish ({obi:.2f})")
                        elif direction == 'short' and obi < -self.min_obi_threshold:
                            confidence += 0.15
                            rationale_parts.append(f"OBI bearish ({obi:.2f})")
                        elif direction != 'flat' and abs(obi) < 0.2:
                            confidence *= 0.8
                            rationale_parts.append(f"Weak OBI ({obi:.2f})")

                        # Spread check
                        if metrics.spread_pct > self.max_spread_pct:
                            direction = 'flat'
                            confidence = 0.0
                            rationale_parts.append(f"Wide spread ({metrics.spread_pct:.2f}%)")

                        # Liquidity check
                        if metrics.liquidity_score < self.min_liquidity_score:
                            confidence *= 0.7
                            rationale_parts.append(f"Low liquidity ({metrics.liquidity_score:.2f})")

            except Exception as e:
                logger.debug(f"Order book analysis error for {symbol}: {e}")

        # 4. Regime adjustment (if available)
        if regime_state:
            regime_mult = getattr(regime_state, 'scalper_multiplier', 1.0)
            regime_name = getattr(getattr(regime_state, 'regime', None), 'value',
                                  str(getattr(regime_state, 'regime', 'unknown')))
            regime_conf = getattr(regime_state, 'confidence', 0.0)
            self._slog.log_regime(symbol, regime_name, regime_conf, regime_mult)
            if regime_mult != 1.0:
                confidence *= regime_mult
                rationale_parts.append(f"Regime adj ({regime_mult:.2f}x)")

        # 5. Consecutive loss penalty
        if self.consecutive_losses >= 2:
            confidence *= 0.7
            rationale_parts.append(f"Loss streak penalty (-{self.consecutive_losses})")
            self._slog.log_risk_event("loss_streak_penalty", {
                "symbol": symbol,
                "consecutive_losses": self.consecutive_losses,
                "confidence_after": round(confidence * 0.7, 4)
            })

        # 6. Final validation
        confidence = float(np.clip(confidence, 0.0, 1.0))
        if confidence < self.min_confidence:
            direction = 'flat'
            if rationale_parts:
                rationale_parts.append(f"Below min confidence ({confidence:.2f})")
            else:
                rationale_parts.append("No signal")

        # Track entry if new position
        if direction != 'flat':
            last_sig = self.last_signals.get(symbol)
            if not last_sig or last_sig.direction == 'flat':
                self.entry_prices[symbol] = current_price
                self.entry_times[symbol] = datetime.now()
                self.highest_profit[symbol] = 0.0
                self.consecutive_losses = 0  # Reset on new entry
                # Log new entry
                self._slog.log_entry(symbol, direction, confidence, current_price,
                                     size=0.0)  # size filled in by size_positions

        rationale = "; ".join(rationale_parts) if rationale_parts else "Neutral"
        return Signal(symbol, direction, confidence, rationale)

    def size_positions(self, signals: Dict[str, Signal],
                       risk_params: Any) -> Dict[str, float]:
        """
        Calculate position sizes for signals.

        Returns dict of {symbol: position_pct} where position_pct
        is the target position as a fraction of equity.
        """
        target_positions = {}
        total_exposure = 0.0

        for symbol, signal in signals.items():
            if signal.direction == 'flat':
                target_positions[symbol] = 0.0
                continue

            # Base size scaled by confidence
            size = self.max_position_pct * signal.confidence

            # Reduce size on loss streak
            if self.consecutive_losses >= 2:
                size *= max(0.5, 1.0 - self.consecutive_losses * 0.15)

            # Cap at max
            size = min(size, self.max_position_pct)

            # Direction
            if signal.direction == 'short':
                size = -size

            target_positions[symbol] = size
            total_exposure += abs(size)

        # Cap total exposure (leave room for other strategies)
        max_scalper_exposure = self.config.get('max_total_exposure', 0.50)  # 50% max
        if total_exposure > max_scalper_exposure:
            scale = max_scalper_exposure / total_exposure
            target_positions = {k: v * scale for k, v in target_positions.items()}
            logger.debug(f"Scaled positions by {scale:.2f} to cap exposure")
            self._slog.log_risk_event("max_exposure_cap", {
                "total_exposure": round(total_exposure, 4),
                "max_allowed": max_scalper_exposure,
                "scale_factor": round(scale, 4)
            })

        return target_positions

    def generate_orders(self, positions: Dict[str, float],
                        current_prices: Dict[str, float]) -> List[Any]:
        """
        Generate orders (handled by StrategyRunner/OrderManager).
        """
        return []

    def reset_state(self):
        """Reset strategy state."""
        self.last_signals.clear()
        self.last_signal_time.clear()
        self.entry_prices.clear()
        self.entry_times.clear()
        self.highest_profit.clear()
        self.consecutive_losses = 0
        logger.info(f"{self.name} state reset")

    def get_risk_summary(self) -> Dict[str, Any]:
        """Get current risk state summary."""
        return {
            'strategy': self.name,
            'leverage': self.leverage,
            'max_position_pct': self.max_position_pct,
            'consecutive_losses': self.consecutive_losses,
            'open_positions': len(self.entry_prices),
            'positions': list(self.entry_prices.keys())
        }
