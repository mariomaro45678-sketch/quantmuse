"""
High-Leverage Risk Management
==============================
Professional risk management for 20x leveraged scalping.

Features:
- Kelly Criterion position sizing
- Dynamic stop loss management
- Trailing stops with breakeven
- Consecutive loss cooldown
- Daily loss limits
- Circuit breakers
- Portfolio heat monitoring
"""

import logging
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskState:
    """Current risk state."""
    level: RiskLevel
    daily_pnl: float = 0.0
    daily_trades: int = 0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    open_positions: int = 0
    portfolio_heat: float = 0.0

    # Limits
    daily_loss_limit_hit: bool = False
    circuit_breaker_triggered: bool = False
    cooldown_active: bool = False
    cooldown_end_time: Optional[datetime] = None  # Timestamp-based cooldown


@dataclass
class PositionRisk:
    """Risk parameters for a single position."""
    symbol: str
    entry_price: float
    position_size: float
    direction: str  # 'long' or 'short'
    leverage: float = 20.0
    
    # Stop levels
    stop_loss: float = 0.0
    take_profit: float = 0.0
    breakeven_price: float = 0.0
    trailing_stop: float = 0.0
    
    # Risk metrics
    risk_amount: float = 0.0  # USD at risk
    risk_pct: float = 0.0  # % of account
    potential_reward: float = 0.0
    risk_reward_ratio: float = 0.0
    
    # State
    breakeven_triggered: bool = False
    trailing_active: bool = False


class HighLeverageRiskManager:
    """
    Professional risk manager for high-leverage scalping.
    
    Implements institutional-grade risk controls for 20x leverage trading.
    """
    
    def __init__(self, config: Dict):
        self.config = config
        
        # Account settings
        self.account_balance = config.get('account_balance', 10000)
        self.leverage = config.get('leverage', 20.0)
        
        # Position sizing
        self.max_position_pct = config.get('max_position_pct', 0.25)  # 25% of account
        self.kelly_fraction = config.get('kelly_fraction', 0.5)  # Half-Kelly
        
        # Stop management
        self.stop_loss_pct = config.get('stop_loss_pct', 0.003)  # 0.3%
        self.take_profit_pct = config.get('take_profit_pct', 0.006)  # 0.6%
        self.breakeven_trigger_pct = config.get('breakeven_trigger_pct', 0.004)  # 0.4%
        self.trailing_trigger_pct = config.get('trailing_trigger_pct', 0.005)  # 0.5%
        self.trailing_distance_pct = config.get('trailing_distance_pct', 0.002)  # 0.2%
        self.time_stop_minutes = config.get('time_stop_minutes', 10)
        
        # Risk limits
        self.daily_loss_limit_pct = config.get('daily_loss_limit_pct', 0.05)  # 5%
        self.circuit_breaker_pct = config.get('circuit_breaker_pct', 0.10)  # 10%
        self.max_consecutive_losses = config.get('max_consecutive_losses', 3)
        self.max_open_positions = config.get('max_open_positions', 3)
        self.cooldown_minutes = config.get('cooldown_minutes', 5)
        
        # State
        self.risk_state = RiskState(level=RiskLevel.NORMAL)
        self.positions: Dict[str, PositionRisk] = {}
        self.trade_history: List[Dict] = []
        self.daily_reset_time = datetime.now().replace(hour=0, minute=0, second=0)
        
        logger.info(f"RiskManager initialized: {self.leverage}x leverage, "
                   f"SL={self.stop_loss_pct:.2%}, TP={self.take_profit_pct:.2%}")
    
    def calculate_position_size(self, symbol: str, direction: str,
                               entry_price: float, confidence: float) -> Tuple[float, PositionRisk]:
        """
        Calculate position size using Kelly Criterion.
        
        Args:
            symbol: Trading symbol
            direction: 'long' or 'short'
            entry_price: Entry price
            confidence: Signal confidence (0-1)
        
        Returns:
            (position_size_usd, risk_params)
        """
        # Check if we can trade
        if not self._can_trade():
            logger.warning("Trading not allowed - risk limits hit")
            return 0.0, None
        
        # Base position size (25% of account)
        base_size = self.account_balance * self.max_position_pct
        
        # Kelly Criterion calculation
        # win_rate = 0.60, avg_win = 0.6%, avg_loss = 0.3%
        win_rate = 0.60
        avg_win = self.take_profit_pct
        avg_loss = self.stop_loss_pct
        
        # Kelly = (W * R - L) / R
        # Where W = win rate, R = avg win, L = avg loss
        kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
        kelly = max(0.1, min(0.5, kelly))  # Cap between 10-50%
        
        # Apply Kelly fraction (half-Kelly for safety)
        kelly_size = base_size * kelly * self.kelly_fraction
        
        # Confidence adjustment
        confidence_adj = 0.5 + (confidence * 0.5)  # 0.5 to 1.0
        
        # Consecutive loss penalty
        loss_penalty = max(0.5, 1.0 - (self.risk_state.consecutive_losses * 0.15))
        
        # Calculate final size
        position_size = kelly_size * confidence_adj * loss_penalty
        
        # Apply leverage
        notional_size = position_size * self.leverage
        
        # Create risk parameters
        risk_params = self._calculate_stops(symbol, direction, entry_price, notional_size)
        
        logger.info(f"Position size for {symbol}: ${notional_size:,.2f} "
                   f"(Kelly={kelly:.2%}, Conf={confidence:.2f}, LossPen={loss_penalty:.2f})")
        
        return notional_size, risk_params
    
    def _calculate_stops(self, symbol: str, direction: str,
                        entry_price: float, position_size: float) -> PositionRisk:
        """Calculate stop loss and take profit levels."""
        if direction == 'long':
            stop_loss = entry_price * (1 - self.stop_loss_pct)
            take_profit = entry_price * (1 + self.take_profit_pct)
            breakeven = entry_price * (1 + self.breakeven_trigger_pct)
        else:
            stop_loss = entry_price * (1 + self.stop_loss_pct)
            take_profit = entry_price * (1 - self.take_profit_pct)
            breakeven = entry_price * (1 - self.breakeven_trigger_pct)
        
        # Calculate risk amount
        risk_per_unit = abs(entry_price - stop_loss)
        units = position_size / entry_price
        risk_amount = risk_per_unit * units
        risk_pct = risk_amount / self.account_balance
        
        # Calculate reward
        reward_per_unit = abs(take_profit - entry_price)
        potential_reward = reward_per_unit * units
        
        risk_reward = potential_reward / risk_amount if risk_amount > 0 else 0
        
        return PositionRisk(
            symbol=symbol,
            entry_price=entry_price,
            position_size=position_size,
            direction=direction,
            leverage=self.leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            breakeven_price=entry_price,  # Move to breakeven
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            potential_reward=potential_reward,
            risk_reward_ratio=risk_reward
        )
    
    def _can_trade(self) -> bool:
        """Check if trading is currently allowed."""
        # Reset daily stats if needed
        self._check_daily_reset()

        # Check if cooldown has expired (timestamp-based, thread-safe)
        self._check_cooldown_expired()

        # Check daily loss limit
        if self.risk_state.daily_pnl <= -self.account_balance * self.daily_loss_limit_pct:
            if not self.risk_state.daily_loss_limit_hit:
                logger.critical(f"Daily loss limit hit: {self.risk_state.daily_pnl:.2%}")
                self.risk_state.daily_loss_limit_hit = True
            return False

        # Check circuit breaker
        if self.risk_state.current_drawdown <= -self.circuit_breaker_pct:
            if not self.risk_state.circuit_breaker_triggered:
                logger.critical(f"Circuit breaker triggered: {self.risk_state.current_drawdown:.2%}")
                self.risk_state.circuit_breaker_triggered = True
            return False

        # Check cooldown (after checking if expired)
        if self.risk_state.cooldown_active:
            return False
        
        # Check max positions
        if len(self.positions) >= self.max_open_positions:
            return False
        
        # Check consecutive losses
        if self.risk_state.consecutive_losses >= self.max_consecutive_losses:
            if not self.risk_state.cooldown_active:
                logger.warning(f"Max consecutive losses ({self.max_consecutive_losses}) reached")
                self._activate_cooldown()
            return False
        
        return True
    
    def _check_daily_reset(self):
        """Check if we need to reset daily stats."""
        now = datetime.now()
        if now.date() != self.daily_reset_time.date():
            logger.info("Resetting daily risk statistics")
            self.risk_state.daily_pnl = 0.0
            self.risk_state.daily_trades = 0
            self.risk_state.daily_loss_limit_hit = False
            self.daily_reset_time = now
    
    def _activate_cooldown(self):
        """Activate trading cooldown using timestamp-based approach (thread-safe)."""
        self.risk_state.cooldown_active = True
        self.risk_state.cooldown_end_time = datetime.now() + timedelta(minutes=self.cooldown_minutes)
        logger.warning(f"Risk cooldown activated for {self.cooldown_minutes} minutes "
                      f"(until {self.risk_state.cooldown_end_time.strftime('%H:%M:%S')})")

    def _check_cooldown_expired(self) -> bool:
        """Check if cooldown has expired and reset if so."""
        if self.risk_state.cooldown_active and self.risk_state.cooldown_end_time:
            if datetime.now() >= self.risk_state.cooldown_end_time:
                self.risk_state.cooldown_active = False
                self.risk_state.cooldown_end_time = None
                self.risk_state.consecutive_losses = 0
                logger.info("Risk cooldown released (expired)")
                return True
        return False
    
    def register_position(self, symbol: str, risk_params: PositionRisk):
        """Register a new position."""
        self.positions[symbol] = risk_params
        self.risk_state.open_positions = len(self.positions)
        self._update_portfolio_heat()
        logger.info(f"Position registered: {symbol} {risk_params.direction} "
                   f"Risk={risk_params.risk_pct:.2%} RR={risk_params.risk_reward_ratio:.2f}")
    
    def close_position(self, symbol: str, exit_price: float, exit_reason: str) -> Dict:
        """
        Close a position and update risk state.
        
        Returns:
            Dict with trade results
        """
        if symbol not in self.positions:
            return None
        
        position = self.positions[symbol]
        
        # Calculate P&L
        if position.direction == 'long':
            pnl_pct = (exit_price - position.entry_price) / position.entry_price * self.leverage * 100
        else:
            pnl_pct = (position.entry_price - exit_price) / position.entry_price * self.leverage * 100
        
        pnl_amount = self.account_balance * (pnl_pct / 100)
        
        # Update risk state
        self.risk_state.daily_pnl += pnl_pct / 100
        self.risk_state.daily_trades += 1
        
        if pnl_pct > 0:
            self.risk_state.consecutive_losses = 0
            self.risk_state.consecutive_wins += 1
        else:
            self.risk_state.consecutive_losses += 1
            self.risk_state.consecutive_wins = 0
        
        # Update drawdown
        if self.risk_state.daily_pnl < self.risk_state.current_drawdown:
            self.risk_state.current_drawdown = self.risk_state.daily_pnl
        if self.risk_state.current_drawdown < self.risk_state.max_drawdown:
            self.risk_state.max_drawdown = self.risk_state.current_drawdown
        
        # Record trade
        trade_record = {
            'symbol': symbol,
            'direction': position.direction,
            'entry': position.entry_price,
            'exit': exit_price,
            'pnl_pct': pnl_pct,
            'pnl_amount': pnl_amount,
            'exit_reason': exit_reason,
            'timestamp': datetime.now()
        }
        self.trade_history.append(trade_record)
        
        # Remove position
        del self.positions[symbol]
        self.risk_state.open_positions = len(self.positions)
        self._update_portfolio_heat()
        
        logger.info(f"Position closed: {symbol} PnL={pnl_pct:.2f}% Reason={exit_reason}")
        
        return trade_record
    
    def update_trailing_stop(self, symbol: str, current_price: float) -> Optional[float]:
        """
        Update trailing stop for a position.
        
        Returns:
            New stop price if updated, None otherwise
        """
        if symbol not in self.positions:
            return None
        
        position = self.positions[symbol]
        
        # Calculate current P&L
        if position.direction == 'long':
            pnl_pct = (current_price - position.entry_price) / position.entry_price
        else:
            pnl_pct = (position.entry_price - current_price) / position.entry_price
        
        # Check breakeven trigger
        if not position.breakeven_triggered and pnl_pct >= self.breakeven_trigger_pct:
            position.breakeven_triggered = True
            logger.info(f"Breakeven triggered for {symbol}")
        
        # Check trailing trigger
        if not position.trailing_active and pnl_pct >= self.trailing_trigger_pct:
            position.trailing_active = True
            logger.info(f"Trailing stop activated for {symbol}")
        
        # Update trailing stop
        if position.trailing_active:
            if position.direction == 'long':
                new_stop = current_price * (1 - self.trailing_distance_pct)
                if new_stop > position.trailing_stop or position.trailing_stop == 0:
                    position.trailing_stop = new_stop
                    return new_stop
            else:
                new_stop = current_price * (1 + self.trailing_distance_pct)
                if new_stop < position.trailing_stop or position.trailing_stop == 0:
                    position.trailing_stop = new_stop
                    return new_stop
        
        return None
    
    def _update_portfolio_heat(self):
        """Calculate portfolio heat (aggregate risk)."""
        if not self.positions:
            self.risk_state.portfolio_heat = 0.0
            return
        
        total_risk = sum(p.risk_amount for p in self.positions.values())
        self.risk_state.portfolio_heat = total_risk / self.account_balance
        
        # Update risk level
        if self.risk_state.portfolio_heat > 0.15:
            self.risk_state.level = RiskLevel.CRITICAL
        elif self.risk_state.portfolio_heat > 0.10:
            self.risk_state.level = RiskLevel.HIGH
        elif self.risk_state.portfolio_heat > 0.05:
            self.risk_state.level = RiskLevel.ELEVATED
        else:
            self.risk_state.level = RiskLevel.NORMAL
    
    def get_exit_levels(self, symbol: str, current_price: float) -> Dict:
        """
        Get current exit levels for a position.
        
        Returns:
            Dict with stop_loss, take_profit, trailing_stop, breakeven
        """
        if symbol not in self.positions:
            return {}
        
        position = self.positions[symbol]
        
        # Determine effective stop loss
        effective_sl = position.stop_loss
        
        # Check breakeven
        if position.breakeven_triggered:
            effective_sl = max(effective_sl, position.breakeven_price) if position.direction == 'long' else min(effective_sl, position.breakeven_price)
        
        # Check trailing
        if position.trailing_active and position.trailing_stop > 0:
            if position.direction == 'long':
                effective_sl = max(effective_sl, position.trailing_stop)
            else:
                effective_sl = min(effective_sl, position.trailing_stop)
        
        return {
            'stop_loss': effective_sl,
            'take_profit': position.take_profit,
            'trailing_stop': position.trailing_stop if position.trailing_active else None,
            'breakeven': position.breakeven_price if position.breakeven_triggered else None,
            'direction': position.direction
        }
    
    def get_risk_report(self) -> Dict:
        """Get comprehensive risk report."""
        return {
            'account_balance': self.account_balance,
            'daily_pnl': self.risk_state.daily_pnl,
            'daily_pnl_pct': self.risk_state.daily_pnl * 100,
            'current_drawdown': self.risk_state.current_drawdown * 100,
            'max_drawdown': self.risk_state.max_drawdown * 100,
            'open_positions': self.risk_state.open_positions,
            'portfolio_heat': self.risk_state.portfolio_heat * 100,
            'risk_level': self.risk_state.level.value,
            'consecutive_losses': self.risk_state.consecutive_losses,
            'consecutive_wins': self.risk_state.consecutive_wins,
            'daily_trades': self.risk_state.daily_trades,
            'can_trade': self._can_trade(),
            'cooldown_active': self.risk_state.cooldown_active,
            'daily_loss_limit_hit': self.risk_state.daily_loss_limit_hit,
            'circuit_breaker_triggered': self.risk_state.circuit_breaker_triggered
        }
    
    def get_trade_statistics(self) -> Dict:
        """Get trade statistics."""
        if not self.trade_history:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'avg_pnl': 0.0,
                'profit_factor': 0.0
            }
        
        total = len(self.trade_history)
        wins = len([t for t in self.trade_history if t['pnl_pct'] > 0])
        losses = total - wins
        
        total_pnl = sum(t['pnl_pct'] for t in self.trade_history)
        avg_pnl = total_pnl / total
        
        gross_profit = sum(t['pnl_pct'] for t in self.trade_history if t['pnl_pct'] > 0)
        gross_loss = abs(sum(t['pnl_pct'] for t in self.trade_history if t['pnl_pct'] < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        return {
            'total_trades': total,
            'wins': wins,
            'losses': losses,
            'win_rate': wins / total * 100,
            'avg_pnl': avg_pnl,
            'profit_factor': profit_factor,
            'total_return': total_pnl
        }


if __name__ == "__main__":
    # Test the risk manager
    config = {
        'account_balance': 10000,
        'leverage': 20.0,
        'max_position_pct': 0.25,
        'stop_loss_pct': 0.003,
        'take_profit_pct': 0.006,
        'daily_loss_limit_pct': 0.05,
        'circuit_breaker_pct': 0.10
    }
    
    rm = HighLeverageRiskManager(config)
    
    # Test position sizing
    size, risk = rm.calculate_position_size('BTC', 'long', 50000, 0.8)
    print(f"Position Size: ${size:,.2f}")
    print(f"Stop Loss: ${risk.stop_loss:,.2f}")
    print(f"Take Profit: ${risk.take_profit:,.2f}")
    print(f"Risk: {risk.risk_pct:.2%}")
    print(f"R:R = 1:{risk.risk_reward_ratio:.2f}")
    
    # Register position
    rm.register_position('BTC', risk)
    
    # Simulate position update
    rm.update_trailing_stop('BTC', 50250)  # +0.5%
    rm.update_trailing_stop('BTC', 50500)  # +1.0% - should trigger trailing
    
    # Get exit levels
    exits = rm.get_exit_levels('BTC', 50500)
    print(f"\nExit Levels:")
    print(f"Stop: ${exits['stop_loss']:,.2f}")
    print(f"Target: ${exits['take_profit']:,.2f}")
    print(f"Trailing: ${exits['trailing_stop']:,.2f}")
    
    # Close position
    result = rm.close_position('BTC', 50700, 'take_profit')
    print(f"\nTrade Result:")
    print(f"PnL: {result['pnl_pct']:.2f}%")
    print(f"Amount: ${result['pnl_amount']:,.2f}")
    
    # Risk report
    report = rm.get_risk_report()
    print(f"\nRisk Report:")
    print(f"Daily PnL: {report['daily_pnl_pct']:.2f}%")
    print(f"Can Trade: {report['can_trade']}")
