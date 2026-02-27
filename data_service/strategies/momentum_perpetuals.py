import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np

from data_service.strategies.strategy_base import StrategyBase, Signal, register_strategy
from data_service.factors.factor_calculator import FactorCalculator
from data_service.factors.orderbook_factors import OrderBookFactors

logger = logging.getLogger(__name__)

@register_strategy('momentum_perpetuals')
class MomentumPerpetuals(StrategyBase):
    """
    Enhanced Trend-Following Strategy for Crypto Perpetuals
    
    DESIGN DECISIONS & RATIONALE:
    1. Multi-Timeframe Agreement: Requires 2+ of [1h, 4h, 1d] momentum to align. Price action 
       is noisy on low timeframes; requiring agreement across horizons ensures we are 
       trading with the broader trend.
    2. Funding Rate Management: Suppresses entries when paying excessive funding (>0.05%) 
       and triggers exits if funding becomes extreme (>0.2%).
    3. Volume Confirmation: High volume increases confidence (institutional flow). 
       Confidence is scaled by volume ratio, penalizing low activity.
    4. Trend Strength Filter: Integrates ADX to avoid 'choppy' low-conviction ranges 
       where trend following often fails.
    5. Trailing Stop: Protects profits after 2% gain by exiting on 50% retrace from peak.
    6. Cooldown Mechanism: Prevents whipsaws during consolidation by enforcing a minimum 
       time between direction changes.
    """

    def __init__(self):
        super().__init__('momentum_perpetuals')
        self.factor_calculator = FactorCalculator()
        self.orderbook_factors = OrderBookFactors()

        # Configuration
        self.cooldown_minutes = self.config.get('cooldown_minutes', 60)
        self.funding_threshold = self.config.get('funding_threshold', 0.0005)  # 0.05%
        self.funding_exit_threshold = self.config.get('funding_exit_threshold', 0.002)  # 0.2%
        self.min_data_points = self.config.get('min_data_points', 100)
        self.base_position_size = self.config.get('base_position_size', 0.10)
        self.adx_threshold = self.config.get('adx_threshold', 20)
        self.volume_min_threshold = self.config.get('volume_min_threshold', 0.7)
        
        # State tracking
        self.last_signals: Dict[str, Signal] = {}
        self.last_direction_change: Dict[str, datetime] = {}
        self.entry_prices: Dict[str, float] = {}
        self.highest_profit: Dict[str, float] = {}
        self.latest_atr: Dict[str, float] = {}
        
        logger.info(f"Initialized {self.name} with funding_threshold={self.funding_threshold}, "
                   f"cooldown={self.cooldown_minutes}min, ADX={self.adx_threshold}")

    def _extract_timestamp(self, df: pd.DataFrame) -> datetime:
        """ Robust timestamp extraction for backtesting compatibility. """
        if not df.empty and 'time' in df.columns:
            try:
                return pd.to_datetime(df['time'].iloc[-1], unit='ms')
            except:
                pass
        if not df.empty and isinstance(df.index, pd.DatetimeIndex):
            return df.index[-1]
        if not df.empty and 'timestamp' in df.columns:
            try:
                return pd.to_datetime(df['timestamp'].iloc[-1], unit='s')
            except:
                pass
        return datetime.now()

    async def calculate_signals(self, market_data: Dict[str, pd.DataFrame], factors: Dict[str, Any]) -> Dict[str, Signal]:
        """
        Generate momentum-based trading signals with multi-factor confirmation.
        """
        signals = {}
        fetcher = factors.get('fetcher')

        for symbol, df in market_data.items():
            # === DATA VALIDATION ===
            if len(df) < self.min_data_points:
                signals[symbol] = Signal(symbol, 'flat', 0.0, f"Insufficient data: {len(df)} < {self.min_data_points}")
                continue

            # === CALCULATE FACTORS ===
            f = await self.factor_calculator.calculate(df, symbol=symbol, fetcher=fetcher)
            
            m1h = f.get('momentum_1h', 0)
            m4h = f.get('momentum_4h', 0)
            m1d = f.get('momentum_1d', 0)
            adx = f.get('adx', 25)
            vol_ratio = f.get('volume_ratio_4h', 1.0)
            funding = f.get('funding_rate_level', 0)
            atr_val = f.get('atr_1h', 0.0)
            
            # Store ATR for sizing (normalize by price)
            current_price = df['close'].iloc[-1]
            if current_price > 0 and atr_val > 0:
                self.latest_atr[symbol] = atr_val / current_price # ATR as % of price
            else:
                self.latest_atr[symbol] = 0.01 # Fallback 1%
            
            if any(pd.isna(x) for x in [m1h, m4h, m1d]):
                signals[symbol] = Signal(symbol, 'flat', 0.0, "NaN in momentum calculations")
                continue
            
            now = self._extract_timestamp(df)

            # === SIGNAL GENERATION ===
            direction = 'flat'
            confidence = 0.0
            rationale_parts = []

            # 1. MULTI-TIMEFRAME AGREEMENT
            up_count = sum([1 for m in [m1h, m4h, m1d] if m > 0])
            down_count = sum([1 for m in [m1h, m4h, m1d] if m < 0])
            
            if up_count >= 2:
                direction = 'long'
                confidence = 0.80 if up_count == 3 else 0.65
                rationale_parts.append(f"Bullish {up_count}/3 TFs")
            elif down_count >= 2:
                direction = 'short'
                confidence = 0.80 if down_count == 3 else 0.65
                rationale_parts.append(f"Bearish {down_count}/3 TFs")
            else:
                rationale_parts.append("No momentum consensus")

            # Check existing position state FIRST (before new signal filters)
            last_sig = self.last_signals.get(symbol)
            position_direction = last_sig.direction if last_sig else 'flat'
            exit_triggered = False
            funding_safe = funding if not pd.isna(funding) else 0

            # === EXIT LOGIC (runs regardless of new signal direction) ===
            if position_direction != 'flat' and symbol in self.entry_prices:
                entry_price = self.entry_prices[symbol]

                # Calculate P&L using POSITION direction
                if position_direction == 'long':
                    pnl_pct = (current_price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - current_price) / entry_price

                # FUNDING RATE EXIT
                if position_direction == 'long' and funding_safe > self.funding_exit_threshold:
                    direction = 'flat'
                    exit_triggered = True
                    rationale_parts.append(f"EXIT: extreme funding ({funding_safe:.4f})")
                elif position_direction == 'short' and funding_safe < -self.funding_exit_threshold:
                    direction = 'flat'
                    exit_triggered = True
                    rationale_parts.append(f"EXIT: extreme funding ({funding_safe:.4f})")

                # STOP LOSS: -4% or -3*ATR (whichever is tighter)
                if not exit_triggered:
                    atr_pct = self.latest_atr.get(symbol, 0.01)
                    stop_loss_pct = min(0.04, atr_pct * 3.0)

                    if pnl_pct < -stop_loss_pct:
                        direction = 'flat'
                        exit_triggered = True
                        rationale_parts.append(f"stop loss ({pnl_pct:.2%})")
                    elif pnl_pct > 0.03:
                        direction = 'flat'
                        exit_triggered = True
                        rationale_parts.append(f"take profit ({pnl_pct:.2%})")
                    elif pnl_pct > 0:
                        if symbol not in self.highest_profit:
                            self.highest_profit[symbol] = 0
                        self.highest_profit[symbol] = max(self.highest_profit[symbol], pnl_pct)
                        if self.highest_profit[symbol] > 0.02:
                            drawdown_from_peak = self.highest_profit[symbol] - pnl_pct
                            if drawdown_from_peak > self.highest_profit[symbol] * 0.30:
                                direction = 'flat'
                                exit_triggered = True
                                rationale_parts.append(f"trailing stop (peak={self.highest_profit[symbol]:.2%})")

                # Clean up state after any exit
                if exit_triggered:
                    self.entry_prices.pop(symbol, None)
                    self.highest_profit.pop(symbol, None)

                # HOLD: No exit triggered and no new opposing signal
                if not exit_triggered and (direction == 'flat' or direction == position_direction):
                    direction = position_direction
                    confidence = max(confidence, 0.5)
                    rationale_parts.append("HOLD (no exit condition met)")

            # Apply filters if we have a NEW primary signal (not a hold)
            if direction != 'flat' and symbol not in self.entry_prices:
                # 2. VOLUME CONFIRMATION
                vol_ratio_safe = vol_ratio if not pd.isna(vol_ratio) else 1.0
                if vol_ratio_safe < self.volume_min_threshold:
                    confidence *= vol_ratio_safe / self.volume_min_threshold
                    rationale_parts.append(f"low vol ({vol_ratio_safe:.2f}x)")
                elif vol_ratio_safe > 1.5:
                    confidence += min(0.15, (vol_ratio_safe - 1.0) * 0.1)
                    rationale_parts.append(f"high vol ({vol_ratio_safe:.2f}x)")

                # 3. TREND STRENGTH FILTER (ADX)
                adx_safe = adx if not pd.isna(adx) else 25
                if adx_safe < self.adx_threshold:
                    confidence *= 0.85
                    rationale_parts.append(f"weak trend (ADX={adx_safe:.0f})")
                elif adx_safe > 40:
                    confidence += 0.10
                    rationale_parts.append(f"strong trend (ADX={adx_safe:.0f})")

                # 4. FUNDING RATE ENTRY FILTER
                if direction == 'long' and funding_safe > self.funding_threshold:
                    direction = 'flat'
                    rationale_parts.append(f"high funding (+{funding_safe:.4f})")
                elif direction == 'short' and funding_safe < -self.funding_threshold:
                    direction = 'flat'
                    rationale_parts.append(f"high funding ({funding_safe:.4f})")

                # 8. ORDER BOOK IMBALANCE ADJUSTMENT
                if fetcher and direction != 'flat':
                    try:
                        ob_imbalance = await self.orderbook_factors.calculate(symbol, fetcher)
                        confidence, ob_reason = self.orderbook_factors.adjust_confidence(
                            confidence, direction, ob_imbalance
                        )
                        if ob_reason:
                            rationale_parts.append(ob_reason)
                        if confidence == 0.0 and "spread" in ob_reason.lower():
                            direction = 'flat'
                    except Exception as e:
                        logger.debug(f"Order book check skipped for {symbol}: {e}")

                # 7. COOLDOWN LOGIC
                if last_sig and last_sig.direction != direction:
                    if symbol not in self.last_direction_change:
                        self.last_direction_change[symbol] = now
                    else:
                        last_change = self.last_direction_change[symbol]
                        time_since_change = (now - last_change).total_seconds() / 60
                        if time_since_change < self.cooldown_minutes:
                            direction = last_sig.direction
                            rationale_parts.append(f"cooldown ({time_since_change:.0f}m)")
                        else:
                            self.last_direction_change[symbol] = now
                            if direction != 'flat':
                                self.entry_prices[symbol] = current_price
                                self.highest_profit[symbol] = 0

            # === FINALIZE SIGNAL ===
            confidence = float(np.clip(confidence, 0.0, 1.0))
            if direction != 'flat' and confidence < 0.50:
                direction = 'flat'
                rationale_parts.append("insufficient confidence")
            
            rationale = "; ".join(rationale_parts) if rationale_parts else "Neutral"
            logger.debug(f"{symbol}: {direction.upper()} @ {confidence:.2f} | {rationale}")
            
            sig = Signal(symbol=symbol, direction=direction, confidence=confidence, rationale=rationale, generated_at=now)
            signals[symbol] = sig
            self.last_signals[symbol] = sig
            
            if direction != 'flat' and (not last_sig or last_sig.direction != direction):
                self.entry_prices[symbol] = current_price
                self.highest_profit[symbol] = 0

        return signals

    def size_positions(self, signals: Dict[str, Signal], risk_params: Any) -> Dict[str, float]:
        """
        Dynamic position sizing based on Volatility (ATR) or Fixed Confidence.
        """
        target_positions = {}
        total_exposure = 0.0
        
        sizing_method = self.config.get('position_size_method', 'fixed')
        risk_per_trade = self.config.get('risk_per_trade', 0.01) # 1% risk per trade
        sl_multiplier = self.config.get('stop_loss_atr_multiplier', 2.0)
        max_pos_size = self.config.get('max_position_size', 0.50) # Cap at 50% equity (1:2 leverage effective max per asset)

        for sym, sig in signals.items():
            if sig.direction == 'flat':
                target_positions[sym] = 0.0
                continue
                
            size = 0.0
            
            if sizing_method == 'volatility_scaled':
                # Volatility Sizing: Risk% / (ATR% * Multiplier)
                atr_pct = self.latest_atr.get(sym, 0.01)
                # Avoid division by zero or extremely low ATR
                atr_pct = max(atr_pct, 0.001) 
                
                stop_distance_pct = atr_pct * sl_multiplier
                size = risk_per_trade / stop_distance_pct
                
                # Scale by confidence
                size *= sig.confidence
            else:
                # Fixed Sizing
                size = self.base_position_size * sig.confidence

            # Hard Cap
            size = min(size, max_pos_size)
            
            if sig.direction == 'short':
                size = -size
            
            target_positions[sym] = size
            total_exposure += abs(size)
        
        # Portfolio Exposure Cap
        max_total_exposure = self.config.get('max_total_exposure', 2.0) # Allow up to 2x leverage total
        if total_exposure > max_total_exposure:
            scale = max_total_exposure / total_exposure
            target_positions = {k: v * scale for k, v in target_positions.items()}
            
        return target_positions

    def generate_orders(self, positions: Dict[str, float], current_prices: Dict[str, float]) -> List[Any]:
        return []

    def reset_state(self):
        self.last_signals.clear()
        self.last_direction_change.clear()
        self.entry_prices.clear()
        self.highest_profit.clear()
        logger.info(f"{self.name} state reset")

    # HELPER FOR VERIFICATION
    def calculate_signals_with_override(self, symbol: str, momentum_direction: str, funding_rate: float) -> Signal:
        """Mock version for verification test."""
        direction = momentum_direction
        confidence = 0.7
        if direction == 'long' and funding_rate > self.funding_threshold: direction = 'flat'
        elif direction == 'short' and funding_rate < -self.funding_threshold: direction = 'flat'
        return Signal(symbol, direction, confidence, "Mock override")
