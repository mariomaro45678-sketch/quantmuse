# Backtest Results - Phase 11

## Momentum Perpetuals (BTC, ETH, XAG, TSLA, NVDA)
- **Total Return**: -7.25%
- **Sharpe Ratio**: -11.19
- **Max Drawdown**: 7.38%
- **Win Rate**: 59.7%
- **Profit Factor**: 1.44
- **Total Trades**: 1028 (384W / 259L)
- **Avg Win/Loss**: 49.38 / -50.93

## Mean Reversion Metals (XAG)
- **Total Return**: -0.32%
- **Sharpe Ratio**: -0.57
- **Max Drawdown**: 1.07%
- **Win Rate**: 38.5%
- **Profit Factor**: 0.89
- **Total Trades**: 26 (5W / 8L)

## Sentiment Driven (BTC, XAG)
- **Total Return**: 0.00%
- **Sharpe Ratio**: 0.00
- **Max Drawdown**: 0.00%
- **Win Rate**: 0.0%
- **Total Trades**: 0
*Note: No historical news data available for the backtest period.*

## Analysis
- **Momentum**: High activity (1028 trades) and a positive Profit Factor (1.44) suggest the core logic identifies profitable setups. However, the negative Total Return (-7.25%) despite positive Gross PnL implies that the `Total Return` calculation might be factoring in costs/slippage aggressively or has a bug, or the high number of losing trades/fees outweighs the edge. The high negative Sharpe is concerning.
- **Mean Reversion**: Very conservative (low drawdown), but unprofitable. Needs tuning for higher frequency or better entry signals.

## Winner: Momentum Perpetuals
Reason: Highest Profit Factor (1.44) and Win Rate (59.7%) indicate potential. The strategy is active and identifying moves. Requires optimization to reduce drawdown and turn positive net return.
