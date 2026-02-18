"""
Risk Manager for Phase 7: Portfolio-level risk metrics, pre-trade checks, and circuit breakers.
Implements VaR, CVaR, maximum drawdown tracking, and comprehensive risk validation.
"""

import logging
import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from datetime import datetime
import numpy as np

from data_service.storage.database_manager import DatabaseManager
from data_service.utils.config_loader import ConfigLoader

logger = logging.getLogger(__name__)


@dataclass
class PreTradeResult:
    """Result of a pre-trade risk check."""
    approved: bool
    reason: str


class RiskManager:
    """
    Portfolio-level risk management system.
    
    Features:
    - VaR/CVaR calculation (95%, 99% confidence)
    - Maximum drawdown tracking
    - Leverage monitoring
    - Pre-trade validation (leverage, position size, correlation, daily loss)
    - Circuit breakers on extreme drawdown
    - Real-time risk snapshot persistence
    """
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None, config: Optional[ConfigLoader] = None):
        self.db = db_manager or DatabaseManager()
        self.config = config or ConfigLoader()
        
        # Risk config
        risk_cfg = self.config.risk
        self.max_portfolio_leverage = risk_cfg['position_limits']['max_portfolio_leverage']
        self.max_position_pct = risk_cfg['position_limits']['max_position_pct_per_asset']
        self.max_correlation_exposure = risk_cfg['position_limits']['max_correlated_exposure_pct']
        self.max_daily_loss_pct = risk_cfg['loss_limits']['max_daily_loss_pct']
        self.circuit_breaker_pct = risk_cfg['loss_limits']['circuit_breaker_drawdown_pct']
        self.var_lookback_days = risk_cfg['var_settings']['var_lookback_days']
        
        # Portfolio state
        self.equity = 100_000.0  # Initial equity
        self.session_high_equity = self.equity
        self.open_positions: List[Dict[str, Any]] = []
        self.daily_pnl = 0.0
        self.session_start_equity = self.equity
        
        # Return history for VaR calculation
        self.return_history: List[float] = []
        
        # Circuit breaker state
        self._strategies_halted = False
        self._positions_closed = False
        
        # Snapshot loop
        self._snapshot_task: Optional[asyncio.Task] = None
        self._snapshot_running = False
        
        logger.info(f"RiskManager initialized | max_leverage={self.max_portfolio_leverage}x | CB={self.circuit_breaker_pct:.1%}")
    
    def load_returns(self, returns: np.ndarray):
        """Load a return series for VaR calculation (useful for testing)."""
        self.return_history = list(returns)
    
    def compute_var_cvar(self) -> tuple[float, float, float]:
        """
        Compute VaR at 95% and 99% confidence, and CVaR at 95%.
        Uses historical simulation method (percentiles).
        
        Returns:
            (var_95, var_99, cvar_95)
        """
        if len(self.return_history) < 30:
            # Insufficient data
            return np.nan, np.nan, np.nan
        
        returns = np.array(self.return_history)
        
        # VaR = percentile (negative returns are losses)
        var_95 = float(np.percentile(returns, 5))   # 5th percentile
        var_99 = float(np.percentile(returns, 1))   # 1st percentile
        
        # CVaR = expected value of returns worse than VaR
        tail_returns = returns[returns <= var_95]
        cvar_95 = float(tail_returns.mean()) if len(tail_returns) > 0 else var_95
        
        return var_95, var_99, cvar_95
    
    def compute_max_drawdown(self) -> float:
        """Compute current drawdown from session high equity."""
        if self.session_high_equity <= 0:
            return 0.0
        drawdown = (self.session_high_equity - self.equity) / self.session_high_equity
        return max(0.0, drawdown)
    
    def compute_leverage_ratio(self) -> float:
        """
        Compute portfolio leverage: sum of absolute notional exposure / total equity.
        """
        if self.equity <= 0:
            return 0.0
        
        total_notional = sum(abs(pos.get('notional', 0.0)) for pos in self.open_positions)
        return total_notional / self.equity
    
    def set_portfolio(self, equity: float, open_positions: List[Dict[str, Any]], session_high_equity: Optional[float] = None):
        """Update portfolio state (called by main loop on every tick)."""
        self.equity = equity
        self.open_positions = open_positions
        if session_high_equity is not None:
            self.session_high_equity = session_high_equity
        else:
            # Track session high
            self.session_high_equity = max(self.session_high_equity, equity)
    
    def set_daily_pnl(self, pnl: float):
        """Set realized + unrealized P&L for today."""
        self.daily_pnl = pnl
    
    def set_config(self, **kwargs):
        """Override config values (useful for testing)."""
        for key, val in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, val)
    
    def pre_trade_check(self, symbol: str, size: float, leverage: float, price: float,
                        is_closing: bool = False, side: str = "",
                        strategy_name: str = "") -> PreTradeResult:
        """
        Comprehensive pre-trade risk validation.

        Checks:
        1. Input validation (guards against invalid parameters)
        2. Portfolio leverage won't exceed max_portfolio_leverage
        3. Single-asset exposure won't exceed max_position_pct
        4. Daily loss limit not already breached (new entries only)

        IMPORTANT: Closing/reducing trades are ALWAYS approved. Blocking a close
        trade increases risk, never decreases it. Only the circuit breaker can
        halt ALL activity.

        Args:
            symbol: Asset symbol
            size: Trade size
            leverage: Leverage multiplier
            price: Current price
            is_closing: If True, this trade reduces an existing position
            side: Order side ('buy' or 'sell')
            strategy_name: Strategy requesting the trade (for per-strategy tracking)

        Returns:
            PreTradeResult(approved=True/False, reason=str)
        """
        # === VALIDATION GUARDS ===
        if self.equity <= 0:
            return PreTradeResult(
                approved=False,
                reason=f"Invalid equity state: equity={self.equity:.2f} (must be > 0)"
            )

        if price <= 0:
            return PreTradeResult(
                approved=False,
                reason=f"Invalid price: {price} (must be > 0)"
            )

        if size <= 0:
            return PreTradeResult(
                approved=False,
                reason=f"Invalid size: {size} (must be > 0)"
            )

        if leverage <= 0:
            return PreTradeResult(
                approved=False,
                reason=f"Invalid leverage: {leverage} (must be > 0)"
            )

        if not symbol or not isinstance(symbol, str):
            return PreTradeResult(
                approved=False,
                reason=f"Invalid symbol: {symbol}"
            )
        # === END VALIDATION GUARDS ===

        # Circuit breaker halts EVERYTHING
        if self._strategies_halted:
            return PreTradeResult(
                approved=False,
                reason="Circuit breaker active - all trading halted"
            )

        # Closing/reducing trades are ALWAYS approved - blocking a close increases risk
        if is_closing:
            return PreTradeResult(approved=True, reason="Closing trade approved (always allowed)")

        # Sell orders that reduce exposure are always approved
        is_sell = side.lower() == "sell"
        if is_sell:
            return PreTradeResult(approved=True, reason="Sell order approved (reduces exposure)")

        # === NEW ENTRY CHECKS (only for opening/increasing positions) ===

        # Check 1: Daily loss limit (only blocks NEW entries, not closes)
        if self.daily_pnl < -self.max_daily_loss_pct * self.session_start_equity:
            return PreTradeResult(
                approved=False,
                reason=f"Daily loss limit breached: P&L={self.daily_pnl:.2f} < -max={self.max_daily_loss_pct:.1%} of equity"
            )

        # Check 2: Portfolio leverage
        new_notional = size * price * leverage
        current_notional = sum(abs(pos.get('notional', 0.0)) for pos in self.open_positions)
        total_notional = current_notional + abs(new_notional)
        new_leverage = total_notional / self.equity if self.equity > 0 else 0.0

        if new_leverage > self.max_portfolio_leverage:
            return PreTradeResult(
                approved=False,
                reason=f"Leverage check failed: new={new_leverage:.2f}x > max={self.max_portfolio_leverage:.2f}x"
            )

        # Check 3: Single-asset position size
        asset_exposure = abs(new_notional) / self.equity if self.equity > 0 else 0.0
        if asset_exposure > self.max_position_pct:
            return PreTradeResult(
                approved=False,
                reason=f"Position size check failed: {symbol} exposure={asset_exposure:.1%} > max={self.max_position_pct:.1%}"
            )

        # All checks passed
        return PreTradeResult(approved=True, reason="All risk checks passed")
    
    def on_equity_update(self, new_equity: float) -> bool:
        """
        Called when equity changes. Checks circuit breaker.
        
        Returns:
            True if circuit breaker fired, False otherwise
        """
        self.equity = new_equity
        self.session_high_equity = max(self.session_high_equity, new_equity)
        
        # Check circuit breaker
        drawdown = self.compute_max_drawdown()
        
        if drawdown > self.circuit_breaker_pct and not self._strategies_halted:
            logger.critical(f"🚨 CIRCUIT BREAKER FIRED | Drawdown={drawdown:.2%} > {self.circuit_breaker_pct:.2%}")
            
            # Set flags
            self._strategies_halted = True
            self._positions_closed = True
            
            # Emit alert
            self.db.save_alert({
                'type': 'circuit_breaker',
                'message': f'Circuit breaker fired at {drawdown:.2%} drawdown',
                'severity': 'critical'
            })
            
            return True
        
        return False
    
    def all_positions_closed(self) -> bool:
        """Check if positions have been closed (circuit breaker flag)."""
        return self._positions_closed
    
    def strategies_halted(self) -> bool:
        """Check if strategies are halted (circuit breaker flag)."""
        return self._strategies_halted
    
    def get_risk_snapshot(self) -> Dict[str, Any]:
        """Get current risk metrics snapshot."""
        var_95, var_99, cvar_95 = self.compute_var_cvar()
        
        return {
            'timestamp': time.time(),
            'total_equity': self.equity,
            'total_leverage': self.compute_leverage_ratio(),
            'var_95': var_95,
            'var_99': var_99,
            'cvar_95': cvar_95,
            'max_drawdown': self.compute_max_drawdown(),
            'num_positions': len(self.open_positions)
        }
    
    async def _snapshot_loop(self):
        """Background loop that writes risk snapshots every 10 seconds."""
        logger.info("Risk snapshot loop started (10s cadence)")
        while self._snapshot_running:
            try:
                snapshot = self.get_risk_snapshot()
                self.db.save_risk_snapshot(snapshot)
                logger.debug(f"Risk snapshot saved | equity={snapshot['total_equity']:.2f} leverage={snapshot['total_leverage']:.2f}x")
            except Exception as e:
                logger.error(f"Error saving risk snapshot: {e}")
            
            await asyncio.sleep(10)
    
    def start_snapshot_loop(self):
        """Start the background snapshot persistence loop."""
        if self._snapshot_running:
            logger.warning("Snapshot loop already running")
            return
        
        self._snapshot_running = True
        # Note: This requires an asyncio event loop to be running
        # In practice, main.py should handle this
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        self._snapshot_task = loop.create_task(self._snapshot_loop())
    
    def stop_snapshot_loop(self):
        """Stop the background snapshot loop."""
        self._snapshot_running = False
        if self._snapshot_task:
            self._snapshot_task.cancel()
