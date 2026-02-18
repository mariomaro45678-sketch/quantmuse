import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Any, Optional, TypedDict
import pandas as pd
import numpy as np

from data_service.utils.config_loader import ConfigLoader

logger = logging.getLogger(__name__)

@dataclass
class Signal:
    """Dataclass representing a trading signal."""
    symbol: str
    direction: str  # 'long' | 'short' | 'flat'
    confidence: float  # 0.0 to 1.0
    rationale: str
    generated_at: datetime = None

    def __post_init__(self):
        if self.generated_at is None:
            self.generated_at = datetime.now()
        if self.direction not in ('long', 'short', 'flat'):
            raise ValueError(f"Invalid direction: {self.direction}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Confidence must be between 0 and 1, got {self.confidence}")


class RiskParams(TypedDict, total=False):
    """Type-safe risk parameters for position sizing."""
    max_position_size: float
    max_leverage: float
    max_drawdown_limit: float
    volatility_target: float


@dataclass
class Trade:
    """Dataclass representing a single trade."""
    timestamp: datetime
    symbol: str
    side: str  # 'buy' | 'sell'
    size: float
    price: float
    value: float
    
    @property
    def is_entry(self) -> bool:
        return abs(self.size) > 0


@dataclass
class BacktestResult:
    """Dataclass representing the results of a strategy backtest."""
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    avg_trade_duration: float  # In hours
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    profit_factor: float
    equity_curve: pd.Series
    trades: List[Trade]
    
    def __str__(self) -> str:
        return (
            f"Backtest Results:\n"
            f"  Total Return: {self.total_return:.2%}\n"
            f"  Sharpe Ratio: {self.sharpe_ratio:.2f}\n"
            f"  Max Drawdown: {self.max_drawdown:.2%}\n"
            f"  Win Rate: {self.win_rate:.2%}\n"
            f"  Total Trades: {self.total_trades}\n"
            f"  Profit Factor: {self.profit_factor:.2f}"
        )


class StrategyBase(ABC):
    """
    Abstract Base Class for all trading strategies.
    
    DESIGN DECISIONS & IMPROVEMENTS:
    1. No Look-Ahead Bias: Signals calculated on bar i, executed at bar i+1 open.
    2. Transaction Costs: Configurable slippage and commission included in simulation.
    3. Risk Controls: Max leverage and negative cash (margin) prevention.
    4. Proper Metrics: Comprehensive trade metrics calculated from actual round-trip events.
    5. Short Position Handling: Correct P&L calculation for short trades.
    6. Robust Data Alignment: Ensures all symbols share a common index before starting.
    7. Performance: Optimized lookback windows and vectorized metrics where possible.
    """

    def __init__(self, name: str):
        self.name = name
        self.config = ConfigLoader().get_strategy_config(name) or {}
        
        # Backtest parameters
        self.commission = self.config.get('commission', 0.001)  # 0.1%
        self.slippage = self.config.get('slippage', 0.0005)  # 0.05%
        self.initial_capital = self.config.get('initial_capital', 100000.0)
        self.max_leverage = self.config.get('max_leverage', 1.0)
        
        logger.info(f"Initialized strategy: {self.name}")

    @abstractmethod
    async def calculate_signals(self, market_data: Dict[str, pd.DataFrame], factors: Dict[str, Any]) -> Dict[str, Signal]:
        """Calculate trading signals based on market data."""
        pass

    @abstractmethod
    def size_positions(self, signals: Dict[str, Signal], risk_params: RiskParams) -> Dict[str, float]:
        """Determine target position sizes based on signals."""
        pass

    @abstractmethod
    def generate_orders(self, positions: Dict[str, float], current_prices: Dict[str, float]) -> List[Any]:
        """Convert target positions into order objects."""
        pass

    def _align_and_validate_data(self, candles: Dict[str, pd.DataFrame]) -> tuple[Dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """Align data across all symbols to a common intersection index."""
        if not candles:
            raise ValueError("No candle data provided")
        
        indices = [df.index for df in candles.values()]
        common_index = indices[0]
        for idx in indices[1:]:
            common_index = common_index.intersection(idx)
        
        if len(common_index) == 0:
            raise ValueError("No common timestamps across all symbols")
        
        logger.info(f"Aligned data: {len(common_index)} common bars across {len(candles)} symbols")
        aligned = {sym: df.loc[common_index] for sym, df in candles.items()}
        return aligned, common_index

    def _calculate_avg_trade_duration(self, trades: List[Trade], timeframe_hours: float) -> float:
        """Calculate average trade duration in hours from round-trip trades."""
        if len(trades) < 2:
            return 0.0

        # Group trades by symbol and find round trips
        symbol_trades: Dict[str, List[Trade]] = {}
        for trade in trades:
            if trade.symbol not in symbol_trades:
                symbol_trades[trade.symbol] = []
            symbol_trades[trade.symbol].append(trade)

        durations = []
        for symbol, sym_trades in symbol_trades.items():
            position = 0.0
            entry_time = None
            for trade in sym_trades:
                if position == 0 and trade.size != 0:
                    # Opening a position
                    position = trade.size
                    entry_time = trade.timestamp
                elif position != 0:
                    # Check if closing
                    if (position > 0 and trade.size < 0) or (position < 0 and trade.size > 0):
                        close_size = min(abs(position), abs(trade.size))
                        if entry_time is not None:
                            duration = (trade.timestamp - entry_time).total_seconds() / 3600
                            durations.append(duration)
                        position += trade.size
                        if abs(position) < 1e-8:
                            position = 0.0
                            entry_time = None
                    else:
                        position += trade.size

        return float(np.mean(durations)) if durations else 0.0

    def _calculate_trade_metrics(self, trades: List[Trade], equity_curve: pd.Series) -> dict:
        """Calculate comprehensive trading metrics from trade history."""
        if not trades:
            return {
                'win_rate': 0.0, 'winning_trades': 0, 'losing_trades': 0,
                'avg_win': 0.0, 'avg_loss': 0.0, 'profit_factor': 0.0
            }
        
        symbol_trades = {}
        for trade in trades:
            if trade.symbol not in symbol_trades: symbol_trades[trade.symbol] = []
            symbol_trades[trade.symbol].append(trade)
        
        pnls = []
        for symbol, symbol_trade_list in symbol_trades.items():
            position = 0.0
            entry_price = 0.0
            for trade in symbol_trade_list:
                if position == 0:
                    position = trade.size
                    entry_price = trade.price
                elif (position > 0 and trade.size < 0) or (position < 0 and trade.size > 0):
                    close_size = min(abs(position), abs(trade.size))
                    pnl = close_size * (trade.price - entry_price) if position > 0 else close_size * (entry_price - trade.price)
                    pnls.append(pnl)
                    position += trade.size
                    if abs(position) < 1e-8:
                        position = 0.0
                        entry_price = 0.0
                else:
                    position += trade.size
        
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total_trades = len(wins) + len(losses)
        win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0.0)
        
        return {
            'win_rate': win_rate, 'winning_trades': len(wins), 'losing_trades': len(losses),
            'avg_win': avg_win, 'avg_loss': avg_loss, 'profit_factor': profit_factor
        }

    def reset_state(self):
        """Reset strategy state. Override in subclasses that maintain state."""
        pass

    def _detect_timeframe_hours(self, index: pd.DatetimeIndex) -> float:
        """Detect the timeframe in hours from the data index."""
        if len(index) < 2:
            return 1.0  # Default to hourly
        diffs = index.to_series().diff().dropna()
        median_diff = diffs.median()
        hours = median_diff.total_seconds() / 3600
        return max(hours, 1/60)  # Minimum 1 minute

    def _annualization_factor(self, timeframe_hours: float) -> float:
        """Calculate annualization factor based on timeframe."""
        periods_per_year = (365 * 24) / timeframe_hours
        return np.sqrt(periods_per_year)

    async def backtest(self, candles: Dict[str, pd.DataFrame], start: int = 0, end: Optional[int] = None) -> BacktestResult:
        """Orchestrate a backtest loop with look-ahead bias prevention."""
        logger.info(f"Starting backtest for {self.name}...")

        # Reset strategy state to prevent leakage between backtests
        self.reset_state()

        aligned_candles, common_index = self._align_and_validate_data(candles)
        
        if end is None or end > len(common_index): end = len(common_index)
        if start >= end: raise ValueError(f"Invalid range: start={start}, end={end}")
        
        cash = self.initial_capital
        positions = {sym: 0.0 for sym in aligned_candles.keys()}
        trades: List[Trade] = []
        equity_curve = np.zeros(end - start)
        
        execution_prices = {}
        for sym, df in aligned_candles.items():
            execution_prices[sym] = df['open'].shift(-1) if 'open' in df.columns else df['close'].shift(-1)
        
        for i in range(start, end - 1):
            # 1. Calculate Signals at bar i
            lookback_start = max(0, i - 199)
            data_slice = {sym: df.iloc[lookback_start:i+1].copy() for sym, df in aligned_candles.items()}
            signals = await self.calculate_signals(data_slice, factors={})
            
            # 2. Size & Equity calculation
            current_prices = {sym: df.iloc[i]['close'] for sym, df in aligned_candles.items()}
            current_equity = cash + sum(positions[sym] * current_prices[sym] for sym in positions.keys())
            equity_curve[i - start] = current_equity
            
            target_positions = self.size_positions(signals, {'max_leverage': self.max_leverage})
            
            # 3. Execute at NEXT bar's open (bar i+1)
            next_idx = common_index[i+1]
            for sym in positions.keys():
                target_pct = target_positions.get(sym, 0.0)
                target_val = current_equity * target_pct
                exec_price = execution_prices[sym].iloc[i]
                
                if pd.isna(exec_price): continue
                
                # Apply Slippage
                if target_val > positions[sym] * exec_price: exec_price *= (1 + self.slippage)
                elif target_val < positions[sym] * exec_price: exec_price *= (1 - self.slippage)
                
                trade_val = target_val - (positions[sym] * exec_price)
                if abs(trade_val) > 1.0:
                    comms = abs(trade_val) * self.commission
                    if cash - comms < -current_equity * 0.1: continue
                    
                    trade_size = trade_val / exec_price
                    positions[sym] += trade_size
                    cash -= (trade_val + comms)
                    trades.append(Trade(next_idx, sym, 'buy' if trade_size > 0 else 'sell', trade_size, exec_price, trade_val))
        
        final_prices = {sym: df.iloc[end-1]['close'] for sym, df in aligned_candles.items()}
        final_equity = cash + sum(positions[sym] * final_prices[sym] for sym in positions.keys())
        equity_curve[-1] = final_equity
        
        equity_series = pd.Series(equity_curve, index=common_index[start:end])
        total_ret = (final_equity - self.initial_capital) / self.initial_capital
        
        # Performance Metrics
        returns = equity_series.pct_change().dropna()
        timeframe_hours = self._detect_timeframe_hours(common_index)
        annualization = self._annualization_factor(timeframe_hours)
        sharpe = (returns.mean() / returns.std()) * annualization if len(returns) > 1 and returns.std() > 0 else 0.0
        peak = equity_series.cummax()
        max_dd = abs(((equity_series - peak) / peak).min()) if len(peak) > 0 else 0.0
        trade_metrics = self._calculate_trade_metrics(trades, equity_series)
        avg_trade_duration = self._calculate_avg_trade_duration(trades, timeframe_hours)
        
        return BacktestResult(total_ret, sharpe, max_dd, trade_metrics['win_rate'], avg_trade_duration, len(trades),
                              trade_metrics['winning_trades'], trade_metrics['losing_trades'],
                              trade_metrics['avg_win'], trade_metrics['avg_loss'], trade_metrics['profit_factor'],
                              equity_series, trades)

# Strategy Registry
STRATEGY_REGISTRY: Dict[str, type] = {}

def register_strategy(name: str):
    """Decorator to register a strategy class."""
    def decorator(cls):
        STRATEGY_REGISTRY[name] = cls
        return cls
    return decorator
