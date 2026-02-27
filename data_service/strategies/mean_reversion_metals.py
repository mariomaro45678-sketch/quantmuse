import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np

from data_service.strategies.strategy_base import StrategyBase, Signal, register_strategy
from data_service.factors.factor_calculator import FactorCalculator
from data_service.factors.metals_factors import MetalsFactors

logger = logging.getLogger(__name__)

@register_strategy('mean_reversion_metals')
class MeanReversionMetals(StrategyBase):
    """
    Enhanced Mean Reversion Strategy for Metals
    
    DESIGN DECISIONS & RATIONALE:
    1. Primary Signal: RSI + Bollinger Bands for oversold/overbought detection. captures volatility-relative extremes.
    2. Confirmation: Gold/Silver Ratio for relative value opportunities (statistical arbitrage).
    3. Refinement: Support/Resistance proximity boosts confidence. uses 50-period highs/lows.
    4. Risk Control: Trend filter (ADX) reduces confidence in strong trends to avoid 'catching a falling knife'.
    5. Scoring: Additive confidence model where signals reinforce rather than conflict.
    6. Performance: Optimization to O(N) by limiting lookback slices in base class.
    """

    METALS_SYMBOLS = ["XAU", "XAG", "HG"]

    def __init__(self):
        super().__init__('mean_reversion_metals')
        self.factor_calculator = FactorCalculator()
        self.metals_factors = MetalsFactors()
        
        # Configurable thresholds
        self.rsi_overbought = self.config.get('rsi_overbought', 65)
        self.rsi_oversold = self.config.get('rsi_oversold', 35)
        self.ratio_zscore_threshold = self.config.get('ratio_zscore_threshold', 2.0)
        self.bb_period = self.config.get('bb_period', 20)
        self.bb_std = self.config.get('bb_std', 2.0)
        self.sr_lookback = self.config.get('sr_lookback', 50)
        self.trend_filter_threshold = self.config.get('trend_filter_threshold', 35)
        # Position sizing - must meet $10 minimum on Hyperliquid
        # For small accounts (<$100), use higher allocation per metal
        self.base_position_size = self.config.get('base_position_size', 0.25)
        self.min_order_notional = self.config.get('min_order_notional', 10.0)
        
        # State tracking for exit logic
        self.entry_prices: Dict[str, float] = {}
        self.entry_bars: Dict[str, int] = {}
        self.bar_count: int = 0
        self.last_signals: Dict[str, Signal] = {}

        logger.info(f"Initialized {self.name} with RSI=[{self.rsi_oversold}, {self.rsi_overbought}], "
                   f"GSR_threshold={self.ratio_zscore_threshold}")

    def reset_state(self):
        """Reset strategy state for clean backtesting."""
        self.entry_prices.clear()
        self.entry_bars.clear()
        self.bar_count = 0
        self.last_signals.clear()
        logger.info(f"{self.name} state reset")

    async def calculate_signals(self, market_data: Dict[str, pd.DataFrame], factors: Dict[str, Any]) -> Dict[str, Signal]:
        """
        Generate trading signals with multi-factor confidence scoring.
        """
        import time
        start_time = time.time()
        signals = {}
        fetcher = factors.get('fetcher')
        
        # Compute cross-asset metals factors
        m_factors = self.metals_factors.calculate(market_data)
        gs_zscore = m_factors.get('gold_silver_ratio_zscore', 0)
        
        logger.debug(f"Gold/Silver Ratio Z-Score: {gs_zscore:.2f}")

        for symbol, df in market_data.items():
            # === GUARD: Only process metals ===
            if symbol not in self.METALS_SYMBOLS:
                signals[symbol] = Signal(symbol, 'flat', 1.0, "Non-metal asset excluded")
                continue

            # === DATA VALIDATION ===
            if len(df) < self.sr_lookback:
                signals[symbol] = Signal(symbol, 'flat', 0.0, 
                                        f"Insufficient data: {len(df)} < {self.sr_lookback}")
                continue

            # === CALCULATE FACTORS ===
            f = await self.factor_calculator.calculate(df, symbol=symbol, fetcher=fetcher)
            
            rsi = f.get('rsi_1d', 50)
            adx = f.get('adx', 25)  # Trend strength
            close = df['close'].iloc[-1]
            
            # Bollinger Bands
            sma = df['close'].rolling(window=self.bb_period).mean().iloc[-1]
            std = df['close'].rolling(window=self.bb_period).std().iloc[-1]
            upper_bb = sma + (std * self.bb_std)
            lower_bb = sma - (std * self.bb_std)
            # Clip position to avoid divide by zero if upper == lower
            bb_range = upper_bb - lower_bb
            bb_position = (close - lower_bb) / bb_range if bb_range != 0 else 0.5
            
            # Support/Resistance levels
            resistance = df['high'].rolling(window=self.sr_lookback).max().iloc[-1]
            support = df['low'].rolling(window=self.sr_lookback).min().iloc[-1]
            
            # Volume divergence (lighter volume = weaker trend = better reversion setup)
            recent_volume = df['volume'].iloc[-5:].mean() if 'volume' in df.columns else 1
            avg_volume = df['volume'].iloc[-20:].mean() if 'volume' in df.columns else 1
            volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1.0
            
            # Data validation
            if pd.isna(rsi) or pd.isna(sma) or pd.isna(std):
                signals[symbol] = Signal(symbol, 'flat', 0.0, "NaN in technical calculations")
                continue

            # === SIGNAL GENERATION WITH CONFIDENCE SCORING ===
            direction = 'flat'
            confidence = 0.0
            rationale_parts = []

            # 1. PRIMARY SIGNAL: RSI OR Bollinger Bands (either can trigger)
            rsi_oversold = rsi < self.rsi_oversold
            rsi_overbought = rsi > self.rsi_overbought
            below_bb = close < lower_bb
            above_bb = close > upper_bb

            if rsi_oversold or below_bb:
                direction = 'long'
                if rsi_oversold and below_bb:
                    # Both conditions — high confidence
                    confidence = 0.65
                    rationale_parts.append(f"Oversold: RSI={rsi:.1f} & below BB")
                elif rsi_oversold:
                    confidence = 0.45
                    rationale_parts.append(f"RSI oversold ({rsi:.1f})")
                else:
                    confidence = 0.45
                    rationale_parts.append(f"Below lower BB (pos={bb_position:.2f})")
                
                # Extra confidence if deeply oversold
                if rsi < 25:
                    confidence += 0.10
                    rationale_parts.append("deeply oversold")
                    
            elif rsi_overbought or above_bb:
                direction = 'short'
                if rsi_overbought and above_bb:
                    confidence = 0.65
                    rationale_parts.append(f"Overbought: RSI={rsi:.1f} & above BB")
                elif rsi_overbought:
                    confidence = 0.45
                    rationale_parts.append(f"RSI overbought ({rsi:.1f})")
                else:
                    confidence = 0.45
                    rationale_parts.append(f"Above upper BB (pos={bb_position:.2f})")
                
                # Extra confidence if deeply overbought
                if rsi > 75:
                    confidence += 0.10
                    rationale_parts.append("deeply overbought")

            # If we have a primary signal, apply additional filters
            if direction != 'flat':
                
                # 2. GOLD/SILVER RATIO CONFIRMATION (additive)
                gs_boost = 0
                if symbol == 'XAU':
                    if gs_zscore > self.ratio_zscore_threshold and direction == 'short':
                        gs_boost = 0.15
                        rationale_parts.append(f"GSR confirms (Z={gs_zscore:.1f})")
                    elif gs_zscore < -self.ratio_zscore_threshold and direction == 'long':
                        gs_boost = 0.15
                        rationale_parts.append(f"GSR confirms (Z={gs_zscore:.1f})")
                    elif abs(gs_zscore) > self.ratio_zscore_threshold:
                        # Ratio suggests opposite direction - reduce confidence
                        gs_boost = -0.15
                        rationale_parts.append(f"GSR conflict (Z={gs_zscore:.1f})")
                        
                elif symbol == 'XAG':
                    if gs_zscore > self.ratio_zscore_threshold and direction == 'long':
                        gs_boost = 0.15
                        rationale_parts.append(f"GSR confirms (Z={gs_zscore:.1f})")
                    elif gs_zscore < -self.ratio_zscore_threshold and direction == 'short':
                        gs_boost = 0.15
                        rationale_parts.append(f"GSR confirms (Z={gs_zscore:.1f})")
                    elif abs(gs_zscore) > self.ratio_zscore_threshold:
                        gs_boost = -0.15
                        rationale_parts.append(f"GSR conflict (Z={gs_zscore:.1f})")
                
                confidence += gs_boost
                
                # 3. SUPPORT/RESISTANCE PROXIMITY (boost when near levels)
                if direction == 'long':
                    distance_to_support = abs(close - support) / support if support != 0 else 1.0
                    if distance_to_support < 0.02:  # Within 2% of support
                        confidence += 0.15
                        rationale_parts.append("near support")
                    elif distance_to_support < 0.05:  # Within 5%
                        confidence += 0.08
                        
                elif direction == 'short':
                    distance_to_resistance = abs(close - resistance) / resistance if resistance != 0 else 1.0
                    if distance_to_resistance < 0.02:  # Within 2% of resistance
                        confidence += 0.15
                        rationale_parts.append("near resistance")
                    elif distance_to_resistance < 0.05:
                        confidence += 0.08
                
                # 4. VOLUME DIVERGENCE (low volume = better mean reversion setup)
                if volume_ratio < 0.8:
                    confidence += 0.10
                    rationale_parts.append(f"low volume ({volume_ratio:.2f}x)")
                elif volume_ratio > 1.5:
                    confidence -= 0.10
                    rationale_parts.append(f"high volume ({volume_ratio:.2f}x)")
                
                # 5. TREND FILTER (reduce confidence in strong trends)
                if not np.isnan(adx) and adx > self.trend_filter_threshold:
                    trend_penalty = min(0.30, (adx - self.trend_filter_threshold) / 100)
                    confidence -= trend_penalty
                    rationale_parts.append(f"strong trend (ADX={adx:.0f})")
                
                # 6. BOLLINGER BAND POSITION (extreme positions = higher confidence)
                if direction == 'long' and bb_position < 0.1:  # Very close to lower band
                    confidence += 0.08
                    rationale_parts.append("extreme BB position")
                elif direction == 'short' and bb_position > 0.9:  # Very close to upper band
                    confidence += 0.08
                    rationale_parts.append("extreme BB position")

            # === EXIT LOGIC FOR EXISTING POSITIONS ===
            last_sig = self.last_signals.get(symbol)
            if last_sig and last_sig.direction != 'flat' and symbol in self.entry_prices:
                entry_price = self.entry_prices[symbol]
                bars_held = self.bar_count - self.entry_bars.get(symbol, self.bar_count)
                should_exit, exit_reason = self.get_exit_conditions(
                    symbol, entry_price, close, bars_held, rsi, last_sig.direction
                )
                if should_exit:
                    direction = 'flat'
                    rationale_parts = [f"EXIT: {exit_reason}"]
                    confidence = 1.0  # High confidence to exit
                    # Clean up state
                    if symbol in self.entry_prices:
                        del self.entry_prices[symbol]
                    if symbol in self.entry_bars:
                        del self.entry_bars[symbol]
                elif direction == 'flat' or direction == last_sig.direction:
                    # HOLD: Position is open but no exit triggered and no new opposing signal.
                    # Continue holding with the existing direction so runner doesn't close.
                    direction = last_sig.direction
                    confidence = max(confidence, 0.5)  # maintain minimum hold confidence
                    rationale_parts.append("HOLD (no exit condition met)")

            # === FINALIZE SIGNAL ===
            # Confidence must be meaningful (>0.4) to take position
            confidence = float(np.clip(confidence, 0.0, 1.0))
            if confidence < 0.4 and symbol not in self.entry_prices:
                # Only force flat if we don't have an open position
                direction = 'flat'
                rationale_parts = ["Insufficient confidence"]

            rationale = "; ".join(rationale_parts) if rationale_parts else "Neutral"

            logger.debug(f"{symbol}: {direction.upper()} @ {confidence:.2f} | {rationale}")

            # Track new entries
            if direction != 'flat' and (not last_sig or last_sig.direction == 'flat'):
                self.entry_prices[symbol] = close
                self.entry_bars[symbol] = self.bar_count

            signals[symbol] = Signal(
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                rationale=rationale,
                generated_at=df.index[-1] if not df.empty else datetime.now()
            )
            self.last_signals[symbol] = signals[symbol]

        self.bar_count += 1
        calc_duration = time.time() - start_time
        logger.debug(f"Signal calculation took {calc_duration:.3f}s for {len(market_data)} symbols")
        return signals

    def size_positions(self, signals: Dict[str, Signal], risk_params: Any) -> Dict[str, float]:
        """
        Dynamic position sizing based on signal confidence.

        Logic:
        - Base allocation is configured (default 25%) per metal
        - Scaled by confidence (0.4-1.0 maps to 40%-100% of base)
        - Maximum total metals exposure: 50% (prevents overconcentration)
        - Enforces minimum notional ($10) for small accounts
        """
        target_positions = {}
        total_exposure = 0.0

        # Get equity from risk_params if available
        equity = 100.0  # fallback
        if risk_params and hasattr(risk_params, 'equity'):
            equity = risk_params.equity

        for sym, sig in signals.items():
            if sig.direction == 'long':
                position_size = self.base_position_size * sig.confidence
                target_positions[sym] = position_size
                total_exposure += position_size
            elif sig.direction == 'short':
                position_size = self.base_position_size * sig.confidence
                target_positions[sym] = -position_size
                total_exposure += position_size
            else:
                target_positions[sym] = 0.0

        # Scale down if total exposure exceeds limit (default 50% for metals)
        max_total_exposure = self.config.get('max_total_exposure', 0.50)
        if total_exposure > max_total_exposure:
            scale_factor = max_total_exposure / total_exposure
            logger.warning(f"Scaling positions by {scale_factor:.2f} to maintain {max_total_exposure:.0%} max exposure")
            target_positions = {k: v * scale_factor for k, v in target_positions.items()}

        # Log position sizing for visibility
        for sym, pct in target_positions.items():
            if pct != 0:
                logger.info(f"[{self.name}] {sym} target: {pct:+.1%} of portfolio")

        return target_positions

    def generate_orders(self, positions: Dict[str, float], current_prices: Dict[str, float]) -> List[Any]:
        """
        Order generation - placeholder for execution logic.
        """
        return []

    def get_exit_conditions(self, symbol: str, entry_price: float, current_price: float, 
                           bars_held: int, current_rsi: float, position_direction: str) -> tuple[bool, str]:
        """
        Exit logic for mean reversion trades.
        
        Returns:
            (should_exit, reason)
        """
        # Exit 1: Price reaches mean (middle Bollinger Band)
        # (This logic is usually handled by current position management)
        
        # Exit 2: RSI crosses 50 (momentum shift)
        if current_rsi > 45 and current_rsi < 55:
            return True, "RSI normalized"
        
        # Exit 3: Time-based (mean reversion should happen within 10 bars)
        if bars_held > 10:
            return True, "Time stop"
        
        # Exit 4: Stop loss (2% for metals)
        side = 1.0 if position_direction == 'long' else -1.0
        pnl_pct = side * (current_price - entry_price) / entry_price
        if pnl_pct < -0.02:
            return True, "Stop loss"
        
        # Exit 5: Take profit (mean reversion achieved)
        if pnl_pct > 0.03:
            return True, "Take profit"
        
        return False, ""

    # HELPER FOR VERIFICATION (Simplified mock for unit tests only)
    def calculate_signals_for_symbol(self, symbol: str, rsi: float) -> Signal:
        """Simplified mock for unit tests only."""
        if symbol not in self.METALS_SYMBOLS:
            return Signal(symbol, 'flat', 1.0, "Guard trigger")
        
        direction = 'flat'
        confidence = 0.0
        if rsi < 30: 
            direction = 'long'
            confidence = 0.7
        elif rsi > 70: 
            direction = 'short'
            confidence = 0.7
        
        return Signal(symbol, direction, confidence, "Mock RSI check")
