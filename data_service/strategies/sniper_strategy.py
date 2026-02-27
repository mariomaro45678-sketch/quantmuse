import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np

from data_service.strategies.strategy_base import StrategyBase, Signal, register_strategy
from data_service.factors.factor_calculator import FactorCalculator
from data_service.factors.orderbook_factors import OrderBookFactors

logger = logging.getLogger(__name__)


@register_strategy('sniper')
class SniperStrategy(StrategyBase):
    """
    Ultra-conservative "Sniper" strategy.

    Philosophy: Say NO to 99% of setups. Only enter when multiple independent
    signals converge with high conviction. One position at a time, full equity
    commitment at 10x leverage.

    Entry requires ALL of:
    - 3/3 timeframe momentum agreement (1h + 4h + 1d)
    - ADX > 30 (strong trend)
    - Volume ratio > 1.5x (institutional flow)
    - Regime: trending (not ranging/volatile)
    - No high-impact economic event within 2h
    - Order book imbalance confirms direction
    - Confidence >= 0.80

    Exit rules:
    - Stop loss: 10% of equity (1% price move at 10x)
    - Take profit: 20% of equity (2% price move at 10x)
    - Trailing stop: after 15% equity gain, trail at 50% of peak profit
    - Time stop: 24h max hold
    - Cooldown: 4h after win, 8h after loss
    """

    def __init__(self):
        super().__init__('sniper')
        self.factor_calculator = FactorCalculator()
        self.orderbook_factors = OrderBookFactors()

        # Config
        self.leverage = self.config.get('leverage', 10.0)
        self.stop_loss_pct = self.config.get('stop_loss_pct', 0.10)
        self.take_profit_pct = self.config.get('take_profit_pct', 0.20)
        self.trailing_activation_pct = self.config.get('trailing_activation_pct', 0.15)
        self.trailing_distance_pct = self.config.get('trailing_distance_pct', 0.50)
        self.min_confidence = self.config.get('min_confidence', 0.80)
        self.cooldown_minutes = self.config.get('cooldown_minutes', 240)
        self.loss_cooldown_minutes = self.config.get('loss_cooldown_minutes', 480)
        self.max_hold_hours = self.config.get('max_hold_hours', 24)
        self.adx_threshold = self.config.get('adx_threshold', 30)
        self.volume_threshold = self.config.get('volume_threshold', 1.5)
        self.min_data_points = self.config.get('min_data_points', 100)

        # State
        self.active_position: Optional[Dict[str, Any]] = None  # {symbol, direction, entry_price, entry_time, peak_pnl_pct}
        self.last_trade_time: Optional[datetime] = None
        self.last_trade_was_loss: bool = False

        logger.info(
            f"SniperStrategy initialized: {self.leverage}x leverage, "
            f"SL={self.stop_loss_pct:.0%}, TP={self.take_profit_pct:.0%}, "
            f"min_conf={self.min_confidence}, cooldown={self.cooldown_minutes}min"
        )

    def _extract_timestamp(self, df: pd.DataFrame) -> datetime:
        if not df.empty and 'time' in df.columns:
            try:
                return pd.to_datetime(df['time'].iloc[-1], unit='ms')
            except Exception:
                pass
        if not df.empty and isinstance(df.index, pd.DatetimeIndex):
            return df.index[-1]
        return datetime.now()

    def _is_in_cooldown(self, now: datetime) -> bool:
        if self.last_trade_time is None:
            return False
        cooldown = self.loss_cooldown_minutes if self.last_trade_was_loss else self.cooldown_minutes
        elapsed = (now - self.last_trade_time).total_seconds() / 60
        return elapsed < cooldown

    async def calculate_signals(self, market_data: Dict[str, pd.DataFrame], factors: Dict[str, Any]) -> Dict[str, Signal]:
        signals = {}
        fetcher = factors.get('fetcher')
        regime_state = factors.get('regime')
        now = datetime.now()

        # === EXIT LOGIC: Check active position first ===
        if self.active_position:
            sym = self.active_position['symbol']
            if sym in market_data:
                df = market_data[sym]
                current_price = df['close'].iloc[-1]
                entry_price = self.active_position['entry_price']
                direction = self.active_position['direction']

                # Calculate PnL as % of equity (with leverage)
                if direction == 'long':
                    price_change_pct = (current_price - entry_price) / entry_price
                else:
                    price_change_pct = (entry_price - current_price) / entry_price
                pnl_pct = price_change_pct * self.leverage  # PnL as % of equity

                # Update peak
                if pnl_pct > self.active_position.get('peak_pnl_pct', 0):
                    self.active_position['peak_pnl_pct'] = pnl_pct

                # Check exits
                exit_reason = None
                peak = self.active_position.get('peak_pnl_pct', 0)

                # 1. Stop loss
                if pnl_pct <= -self.stop_loss_pct:
                    exit_reason = f"STOP LOSS hit ({pnl_pct:+.1%} equity)"

                # 2. Take profit
                elif pnl_pct >= self.take_profit_pct:
                    exit_reason = f"TAKE PROFIT hit ({pnl_pct:+.1%} equity)"

                # 3. Trailing stop (after activation threshold)
                elif peak >= self.trailing_activation_pct:
                    trail_level = peak * (1 - self.trailing_distance_pct)
                    if pnl_pct <= trail_level:
                        exit_reason = f"TRAILING STOP (peak={peak:+.1%}, now={pnl_pct:+.1%})"

                # 4. Time stop
                hold_hours = (now - self.active_position['entry_time']).total_seconds() / 3600
                if hold_hours >= self.max_hold_hours:
                    exit_reason = f"TIME STOP ({hold_hours:.1f}h >= {self.max_hold_hours}h)"

                # 5. Economic event exit
                economic_calendar = factors.get('economic_calendar')
                if economic_calendar and not exit_reason:
                    try:
                        cal_mult, cal_reason = economic_calendar.get_trading_multiplier()
                        if cal_mult == 0.0:
                            exit_reason = f"ECONOMIC EVENT EXIT ({cal_reason})"
                    except Exception:
                        pass

                if exit_reason:
                    logger.warning(f"SNIPER EXIT {sym}: {exit_reason}")
                    self.last_trade_time = now
                    self.last_trade_was_loss = pnl_pct < 0
                    self.active_position = None

                    # Signal flat to close
                    signals[sym] = Signal(sym, 'flat', 1.0, exit_reason)
                    # All other symbols flat
                    for other_sym in market_data:
                        if other_sym != sym:
                            signals[other_sym] = Signal(other_sym, 'flat', 0.0, "Position closing")
                    return signals
                else:
                    # HOLD — signal current direction to maintain position
                    signals[sym] = Signal(sym, direction, 0.9, f"HOLD ({pnl_pct:+.1%} equity, {hold_hours:.1f}h)")
                    for other_sym in market_data:
                        if other_sym != sym:
                            signals[other_sym] = Signal(other_sym, 'flat', 0.0, "Already in position")
                    return signals

        # === ENTRY LOGIC: No position, scan all assets ===

        # Check cooldown
        if self._is_in_cooldown(now):
            cooldown = self.loss_cooldown_minutes if self.last_trade_was_loss else self.cooldown_minutes
            elapsed = (now - self.last_trade_time).total_seconds() / 60
            remaining = cooldown - elapsed
            for sym in market_data:
                signals[sym] = Signal(sym, 'flat', 0.0, f"Cooldown ({remaining:.0f}min remaining)")
            return signals

        # Check economic calendar — don't enter near events
        economic_calendar = factors.get('economic_calendar')
        if economic_calendar:
            try:
                cal_mult, cal_reason = economic_calendar.get_trading_multiplier()
                if cal_mult < 0.5:
                    for sym in market_data:
                        signals[sym] = Signal(sym, 'flat', 0.0, f"Near economic event: {cal_reason}")
                    return signals
            except Exception:
                pass

        # Check regime — only trade in trending markets
        if regime_state:
            regime_name = regime_state.regime.value if hasattr(regime_state.regime, 'value') else str(regime_state.regime)
            if 'trending' not in regime_name:
                for sym in market_data:
                    signals[sym] = Signal(sym, 'flat', 0.0, f"Regime not trending: {regime_name}")
                return signals

        # Score each asset
        candidates = []

        for sym, df in market_data.items():
            if len(df) < self.min_data_points:
                signals[sym] = Signal(sym, 'flat', 0.0, f"Insufficient data ({len(df)})")
                continue

            f = await self.factor_calculator.calculate(df, symbol=sym, fetcher=fetcher)

            m1h = f.get('momentum_1h', 0)
            m4h = f.get('momentum_4h', 0)
            m1d = f.get('momentum_1d', 0)
            adx = f.get('adx', 0)
            vol_ratio = f.get('volume_ratio_4h', 1.0)
            funding = f.get('funding_rate_level', 0)

            # Handle NaN
            if any(pd.isna(x) for x in [m1h, m4h, m1d, adx, vol_ratio]):
                signals[sym] = Signal(sym, 'flat', 0.0, "NaN in factors")
                continue

            rejection_reasons = []

            # Filter 1: 3/3 timeframe agreement (STRICT)
            all_bullish = m1h > 0 and m4h > 0 and m1d > 0
            all_bearish = m1h < 0 and m4h < 0 and m1d < 0

            if not (all_bullish or all_bearish):
                up = sum(1 for m in [m1h, m4h, m1d] if m > 0)
                signals[sym] = Signal(sym, 'flat', 0.0, f"No 3/3 TF agreement ({up}/3 bullish)")
                continue

            direction = 'long' if all_bullish else 'short'

            # Filter 2: ADX strength
            if adx < self.adx_threshold:
                signals[sym] = Signal(sym, 'flat', 0.0, f"ADX too low ({adx:.0f} < {self.adx_threshold})")
                continue

            # Filter 3: Volume
            if vol_ratio < self.volume_threshold:
                signals[sym] = Signal(sym, 'flat', 0.0, f"Volume too low ({vol_ratio:.2f}x < {self.volume_threshold}x)")
                continue

            # Filter 4: Funding rate (don't pay excessive funding)
            funding_safe = funding if not pd.isna(funding) else 0
            if direction == 'long' and funding_safe > 0.001:
                signals[sym] = Signal(sym, 'flat', 0.0, f"High funding for long ({funding_safe:.4f})")
                continue
            elif direction == 'short' and funding_safe < -0.001:
                signals[sym] = Signal(sym, 'flat', 0.0, f"High funding for short ({funding_safe:.4f})")
                continue

            # Build confidence score
            confidence = 0.70  # Base for 3/3 agreement

            # ADX bonus (30-50 range)
            adx_bonus = min(0.10, (adx - self.adx_threshold) / 200)
            confidence += adx_bonus

            # Volume bonus
            vol_bonus = min(0.10, (vol_ratio - self.volume_threshold) * 0.05)
            confidence += vol_bonus

            # Momentum strength bonus (stronger = better)
            avg_momentum = abs(np.mean([m1h, m4h, m1d]))
            mom_bonus = min(0.10, avg_momentum * 0.5)
            confidence += mom_bonus

            # Filter 5: Order book imbalance
            if fetcher:
                try:
                    ob_imbalance = await self.orderbook_factors.calculate(sym, fetcher)
                    confidence, ob_reason = self.orderbook_factors.adjust_confidence(
                        confidence, direction, ob_imbalance
                    )
                    if confidence == 0.0:
                        signals[sym] = Signal(sym, 'flat', 0.0, f"Order book rejection: {ob_reason}")
                        continue
                except Exception:
                    # No order book data — reduce confidence but don't reject
                    confidence *= 0.90

            confidence = float(np.clip(confidence, 0.0, 1.0))

            # Filter 6: Minimum confidence
            if confidence < self.min_confidence:
                signals[sym] = Signal(sym, 'flat', 0.0,
                    f"Confidence too low ({confidence:.2f} < {self.min_confidence})")
                continue

            # This asset passes all filters
            rationale = (f"3/3 TF {direction} | ADX={adx:.0f} | vol={vol_ratio:.1f}x | "
                        f"mom=[{m1h:+.3f},{m4h:+.3f},{m1d:+.3f}]")
            candidates.append((sym, direction, confidence, rationale))
            logger.info(f"SNIPER CANDIDATE: {sym} {direction.upper()} conf={confidence:.2f} | {rationale}")

        # Pick the BEST candidate (highest confidence)
        if candidates:
            candidates.sort(key=lambda x: x[2], reverse=True)
            best_sym, best_dir, best_conf, best_rationale = candidates[0]

            # Set active position tracking
            current_price = market_data[best_sym]['close'].iloc[-1]
            self.active_position = {
                'symbol': best_sym,
                'direction': best_dir,
                'entry_price': current_price,
                'entry_time': now,
                'peak_pnl_pct': 0.0,
            }

            logger.warning(f"SNIPER ENTRY: {best_dir.upper()} {best_sym} @ {current_price:.2f} "
                          f"conf={best_conf:.2f} | {best_rationale}")

            signals[best_sym] = Signal(best_sym, best_dir, best_conf, f"SNIPER ENTRY: {best_rationale}")

            # All others flat
            for sym in market_data:
                if sym != best_sym and sym not in signals:
                    signals[sym] = Signal(sym, 'flat', 0.0, "Not selected")
        else:
            # No candidates — all flat
            for sym in market_data:
                if sym not in signals:
                    signals[sym] = Signal(sym, 'flat', 0.0, "No setup found")

        return signals

    def size_positions(self, signals: Dict[str, Signal], risk_params: Any) -> Dict[str, float]:
        """Full equity at configured leverage. One position only.
        Uses 90% of max to leave margin buffer for fees/rounding."""
        target_positions = {}
        effective_leverage = self.leverage * 0.90  # 90% safety margin

        for sym, sig in signals.items():
            if sig.direction == 'flat':
                target_positions[sym] = 0.0
            elif sig.direction == 'long':
                target_positions[sym] = effective_leverage
            elif sig.direction == 'short':
                target_positions[sym] = -effective_leverage

        return target_positions

    def generate_orders(self, positions: Dict[str, float], current_prices: Dict[str, float]) -> List[Any]:
        return []

    def reset_state(self):
        self.active_position = None
        self.last_trade_time = None
        self.last_trade_was_loss = False
        logger.info(f"{self.name} state reset")
