"""
Position Sizer for Phase 7: Kelly Criterion, Volatility Scaling, and Risk Parity sizing methods.
Includes stop-loss management with trailing stops.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from datetime import datetime
import numpy as np

from data_service.risk.risk_manager import RiskManager
from data_service.utils.config_loader import ConfigLoader

logger = logging.getLogger(__name__)


@dataclass
class StopLossOrder:
    """Auto-generated close order when stop-loss is breached."""
    symbol: str
    side: str  # 'sell' for long, 'buy' for short
    size: float
    reason: str


class PositionSizer:
    """
    Position sizing engine supporting three methods:
    1. Kelly Criterion (capped at 25% of full Kelly)
    2. Volatility Scaling (target fixed P&L volatility)
    3. Risk Parity (equal risk allocation across positions)
    
    Also includes stop-loss monitoring and auto-close generation.
    """
    
    def __init__(self, risk_manager: Optional[RiskManager] = None, config: Optional[ConfigLoader] = None):
        self.risk_mgr = risk_manager
        self.config = config or ConfigLoader()
        
        # Stop-loss config
        stop_cfg = self.config.risk['stop_loss_settings']
        self.default_stop_loss_pct = stop_cfg['default_stop_loss_pct']
        self.trailing_stop_enabled = stop_cfg['trailing_stop_enabled']
        self.trailing_activation_pct = stop_cfg['trailing_stop_activation_pct']
        self.trailing_distance_pct = stop_cfg['trailing_stop_distance_pct']
        
        # Tracked positions for stop-loss monitoring
        self._positions: Dict[str, Dict[str, Any]] = {}
        
        # Equity tracking
        self._equity = 100_000.0
        
        logger.info(f"PositionSizer initialized | default_stop={self.default_stop_loss_pct:.1%}")
    
    def set_equity(self, equity: float):
        """Update current equity (used for sizing calculations)."""
        self._equity = equity
    
    def size_kelly(self, win_rate: float, avg_win: float, avg_loss: float, cap_fraction: float = 0.25) -> float:
        """
        Kelly Criterion sizing.
        
        Formula: f* = (bp - q) / b
        where:
            b = avg_win / avg_loss (payoff ratio)
            p = win_rate
            q = 1 - p
        
        Cap at 25% of full Kelly to avoid aggressive sizing.
        
        Args:
            win_rate: Historical win rate (0-1)
            avg_win: Average gain on winning trades
            avg_loss: Average loss on losing trades (positive number)
            cap_fraction: Cap Kelly at this fraction (default 0.25)
        
        Returns:
            Position size as fraction of equity
        """
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0
        
        b = avg_win / avg_loss  # Payoff ratio
        p = win_rate
        q = 1 - p
        
        kelly_fraction = (b * p - q) / b
        
        # Cap at configured fraction (default 25%)
        capped = min(kelly_fraction, cap_fraction)
        
        # Never allow negative sizing
        if capped < 0:
            return 0.0
        
        # Convert to dollar size
        size = capped * self._equity
        
        logger.debug(f"Kelly sizing: win_rate={win_rate:.2%} b={b:.2f} → kelly={kelly_fraction:.2%} capped={capped:.2%} size=${size:.2f}")
        return size
    
    def size_volatility(self, asset_daily_vol: float, target_vol_pct: float = 0.01) -> float:
        """
        Volatility scaling: target a fixed daily P&L volatility per position.
        
        Formula: size = (target_vol_pct × equity) / asset_daily_vol
        
        Args:
            asset_daily_vol: Daily volatility of the asset (e.g., 0.02 for 2%)
            target_vol_pct: Target daily P&L volatility (e.g., 0.01 for 1%)
        
        Returns:
            Position size in dollars
        """
        if asset_daily_vol <= 0:
            return 0.0
        
        size = (target_vol_pct * self._equity) / asset_daily_vol
        
        logger.debug(f"Volatility sizing: asset_vol={asset_daily_vol:.2%} target_vol={target_vol_pct:.2%} → size=${size:.2f}")
        return size
    
    def size_risk_parity(self, num_positions: int, asset_daily_vol: float) -> float:
        """
        Risk Parity: allocate equal risk (dollar volatility) across all open positions.
        
        Each position gets: equity / (num_positions × asset_vol)
        
        Args:
            num_positions: Total number of open positions
            asset_daily_vol: Daily volatility of this specific asset
        
        Returns:
            Position size in dollars
        """
        if num_positions <= 0 or asset_daily_vol <= 0:
            return 0.0
        
        # Each position gets equal share of total equity risk
        size = self._equity / (num_positions * asset_daily_vol)
        
        logger.debug(f"Risk parity sizing: {num_positions} positions × {asset_daily_vol:.2%} vol → size=${size:.2f}")
        return size
    
    def apply_constraints(self, symbol: str, raw_size: float, leverage: float, price: float,
                          min_order_size: float, is_closing: bool = False, side: str = "",
                          strategy_name: str = "") -> float:
        """
        Apply constraints to raw size:
        1. Round up to min_order_size
        2. Pass through RiskManager pre-trade check
        3. Return 0 if checks fail

        Args:
            symbol: Asset symbol
            raw_size: Raw size from sizing method
            leverage: Leverage multiplier
            price: Current price
            min_order_size: Minimum order size from asset config
            is_closing: If True, this is a closing/reducing trade (skip size limits)
            side: Order side ('buy' or 'sell'). Sell orders reduce exposure and are never blocked.
            strategy_name: Strategy requesting the trade

        Returns:
            Constrained size (or 0 if rejected)
        """
        # Round up to min size
        if raw_size < min_order_size:
            size = min_order_size
        else:
            size = raw_size

        # Pre-trade check if risk manager is available
        if self.risk_mgr:
            check = self.risk_mgr.pre_trade_check(
                symbol, size, leverage, price,
                is_closing=is_closing, side=side,
                strategy_name=strategy_name
            )
            if not check.approved:
                logger.warning(f"[{strategy_name}] Size constrained to 0 | {check.reason}")
                return 0.0

        return size
    
    def register_position(self, symbol: str, entry_price: float, direction: str,
                          position_size: float, stop_loss_pct: Optional[float] = None):
        """
        Register a position for stop-loss monitoring.

        Args:
            symbol: Asset symbol
            entry_price: Entry price
            direction: 'long' or 'short'
            position_size: Actual position size in units (REQUIRED)
            stop_loss_pct: Stop-loss percentage (uses default if None)
        """
        # Validate inputs
        if entry_price <= 0:
            logger.error(f"Cannot register position: invalid entry_price={entry_price}")
            return
        if direction not in ('long', 'short'):
            logger.error(f"Cannot register position: invalid direction={direction}")
            return
        if position_size <= 0:
            logger.error(f"Cannot register position: invalid position_size={position_size}")
            return

        stop_pct = stop_loss_pct or self.default_stop_loss_pct

        if direction == 'long':
            stop_price = entry_price * (1 - stop_pct)
        else:  # short
            stop_price = entry_price * (1 + stop_pct)

        self._positions[symbol] = {
            'entry_price': entry_price,
            'direction': direction,
            'size': position_size,  # Store actual position size
            'stop_price': stop_price,
            'highest_price': entry_price,  # For trailing stops
            'trailing_active': False,
            'status': 'active'  # For atomic state management
        }

        logger.info(f"Position registered | {symbol} {direction} {position_size:.4f} @ {entry_price:.2f} stop={stop_price:.2f}")
    
    def on_price_tick(self, symbol: str, current_price: float) -> Optional[StopLossOrder]:
        """
        Monitor price for stop-loss breach.
        
        Args:
            symbol: Asset symbol
            current_price: Current market price
        
        Returns:
            StopLossOrder if stop breached, None otherwise
        """
        if symbol not in self._positions:
            return None
        
        pos = self._positions[symbol]
        direction = pos['direction']
        stop_price = pos['stop_price']
        
        # Update trailing stop if enabled
        if self.trailing_stop_enabled:
            if direction == 'long':
                # Update highest price
                if current_price > pos['highest_price']:
                    pos['highest_price'] = current_price
                
                # Activate trailing if price rose enough
                if not pos['trailing_active']:
                    gain_pct = (current_price - pos['entry_price']) / pos['entry_price']
                    if gain_pct >= self.trailing_activation_pct:
                        pos['trailing_active'] = True
                        logger.info(f"Trailing stop activated | {symbol} gain={gain_pct:.2%}")
                
                # Update trailing stop
                if pos['trailing_active']:
                    trailing_stop = pos['highest_price'] * (1 - self.trailing_distance_pct)
                    pos['stop_price'] = max(stop_price, trailing_stop)
            
            else:  # short
                # Update lowest price
                if current_price < pos['highest_price']:
                    pos['highest_price'] = current_price
                
                # Activate trailing
                if not pos['trailing_active']:
                    gain_pct = (pos['entry_price'] - current_price) / pos['entry_price']
                    if gain_pct >= self.trailing_activation_pct:
                        pos['trailing_active'] = True
                        logger.info(f"Trailing stop activated | {symbol} gain={gain_pct:.2%}")
                
                # Update trailing stop
                if pos['trailing_active']:
                    trailing_stop = pos['highest_price'] * (1 + self.trailing_distance_pct)
                    pos['stop_price'] = min(stop_price, trailing_stop)
        
        # Check if stop breached
        stop_breached = False
        if direction == 'long' and current_price <= pos['stop_price']:
            stop_breached = True
        elif direction == 'short' and current_price >= pos['stop_price']:
            stop_breached = True
        
        if stop_breached:
            # Check if already pending close (prevent duplicate orders)
            if pos.get('status') == 'pending_close':
                logger.debug(f"Stop-loss already pending close for {symbol}")
                return None

            # Get actual position size (with fallback for legacy positions)
            position_size = pos.get('size', 1.0)
            if position_size <= 0:
                logger.error(f"Invalid position size for {symbol}: {position_size}")
                return None

            logger.warning(f"STOP-LOSS BREACHED | {symbol} {direction} {position_size:.4f} "
                          f"price={current_price:.2f} stop={pos['stop_price']:.2f}")

            # Mark as pending close (atomic: don't delete yet)
            pos['status'] = 'pending_close'

            # Generate close order with actual size
            close_order = StopLossOrder(
                symbol=symbol,
                side='sell' if direction == 'long' else 'buy',
                size=position_size,  # Use actual position size
                reason=f"Stop-loss breached at {current_price:.2f} (stop={pos['stop_price']:.2f})"
            )

            # Note: Position will be removed via confirm_stop_loss_executed() or remove_position()
            # This makes the operation atomic - position stays in tracking until confirmed

            return close_order

        return None

    def confirm_stop_loss_executed(self, symbol: str, success: bool):
        """
        Confirm that a stop-loss order was executed (or failed).
        Called after attempting to execute the stop-loss order.

        Args:
            symbol: Asset symbol
            success: True if order executed successfully, False otherwise
        """
        if symbol not in self._positions:
            return

        pos = self._positions[symbol]

        if success:
            # Order executed - remove from tracking
            del self._positions[symbol]
            logger.info(f"Stop-loss confirmed executed | {symbol} removed from tracking")
        else:
            # Order failed - revert to active status
            pos['status'] = 'active'
            logger.warning(f"Stop-loss execution failed | {symbol} reverted to active")
    
    def remove_position(self, symbol: str):
        """Remove position from stop-loss tracking (after manual close)."""
        if symbol in self._positions:
            del self._positions[symbol]
            logger.debug(f"Position removed from tracking: {symbol}")
