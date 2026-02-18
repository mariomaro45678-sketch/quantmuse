import argparse
import asyncio
import logging
import sys
from datetime import datetime
from typing import List, Dict, Any

import pandas as pd
import numpy as np

# Add the project root to sys.path to allow running from scripts/
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from data_service.strategies.strategy_base import STRATEGY_REGISTRY
# Ensure strategies are registered
import data_service.strategies.momentum_perpetuals
import data_service.strategies.mean_reversion_metals
import data_service.strategies.sentiment_driven

from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher
from data_service.storage.database_manager import DatabaseManager
from data_service.utils.config_loader import get_config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("BacktestRunner")

async def run_backtest(strategy_name: str, symbols: List[str], limit: int, mode: str):
    """
    Main execution loop for a strategy backtest.
    """
    logger.info(f"--- Running Backtest: {strategy_name} ---")
    logger.info(f"Assets: {symbols} | Limit: {limit} | Mode: {mode}")

    # 1. Setup Data Source
    candles = {}
    
    if mode == 'db':
        logger.info("Loading data from Local Database...")
        db = DatabaseManager()
        for symbol in symbols:
            try:
                data = db.get_candles(symbol, '1h', limit=limit)
                if not data:
                    logger.warning(f"No DB data found for {symbol}")
                    continue
                
                df = pd.DataFrame(data)
                # Ensure columns are float
                cols = ['open', 'high', 'low', 'close', 'volume']
                for c in cols:
                    df[c] = df[c].astype(float)
                    
                # Ensure time is parsed if needed, though strategy extracts it
                candles[symbol] = df
                logger.info(f"Loaded {len(df)} candles for {symbol}")
            except Exception as e:
                logger.error(f"Failed to load DB data for {symbol}: {e}")
                
    else:
        # Live/Mock Fetcher
        fetcher = HyperliquidFetcher(mode=mode)
        
        # 2. Fetch Data
        for symbol in symbols:
            try:
                logger.info(f"Fetching {limit} candles for {symbol}...")
                df = await fetcher.get_candles(symbol, '1h', limit=limit)
                if df.empty:
                    logger.warning(f"No data returned for {symbol}")
                    continue
                candles[symbol] = df
            except Exception as e:
                logger.error(f"Failed to fetch data for {symbol}: {e}")

    if not candles:
        logger.error("No data available to backtest.")
        return

    # 3. Instantiate Strategy
    if strategy_name not in STRATEGY_REGISTRY:
        logger.error(f"Strategy '{strategy_name}' not found in registry. Options: {list(STRATEGY_REGISTRY.keys())}")
        return

    strat_cls = STRATEGY_REGISTRY[strategy_name]
    strategy = strat_cls()

    # 4. Run Backtest
    try:
        start_time = datetime.now()
        result = await strategy.backtest(candles)
        duration = datetime.now() - start_time
        
        # 5. Report Results
        logger.info("\n" + "="*50)
        logger.info(f" BACKTEST RESULTS: {strategy_name.upper()} ")
        logger.info("="*50)
        logger.info(f"Duration:        {duration.total_seconds():.2f}s")
        logger.info(f"Total Return:    {result.total_return * 100:.2f}%")
        logger.info(f"Sharpe Ratio:    {result.sharpe_ratio:.2f}")
        logger.info(f"Max Drawdown:    {result.max_drawdown * 100:.2f}%")
        logger.info(f"Win Rate:        {result.win_rate * 100:.1f}%")
        logger.info(f"Profit Factor:   {result.profit_factor:.2f}")
        logger.info(f"Total Trades:    {result.total_trades} ({result.winning_trades}W / {result.losing_trades}L)")
        logger.info(f"Avg Win/Loss:    {result.avg_win:.2f} / {result.avg_loss:.2f}")
        logger.info(f"OOS Validation:  {'N/A'}")
        logger.info("="*50)

        # ASCII Equity Curve (Simple representation)
        if not result.equity_curve.empty:
            logger.info("\nEquity Curve (Normalized):")
            # Normalize to 1.0 starting point
            initial_val = result.equity_curve.iloc[0] if result.equity_curve.iloc[0] != 0 else 1.0
            norm_curve = result.equity_curve / initial_val
            min_eq = norm_curve.min()
            max_eq = norm_curve.max()
            logger.info(f"Min: {min_eq:.4f} | Max: {max_eq:.4f}")
            
            # Risk Metrics (VaR)
            returns = result.equity_curve.pct_change().dropna()
            if not returns.empty:
                var_95 = np.percentile(returns, 5)
                cvar_95 = returns[returns <= var_95].mean()
                logger.info(f"95% VaR:     {var_95:.4%}")
                logger.info(f"95% CVaR:    {cvar_95:.4%}")

    except Exception as e:
        logger.error(f"Backtest execution failed: {e}")
        import traceback
        traceback.print_exc()

def main():
    parser = argparse.ArgumentParser(description="QuantMuse Backtesting Engine CLI")
    parser.add_argument("--strategy", type=str, required=True, help="Strategy name (e.g., momentum_perpetuals)")
    parser.add_argument("--symbols", type=str, nargs='+', default=["XAU"], help="Assets to backtest (space separated)")
    parser.add_argument("--limit", type=int, default=500, help="Number of candles (1h timeframe)")
    parser.add_argument("--mode", type=str, choices=['mock', 'live', 'db'], default='mock', help="Execution mode")

    args = parser.parse_args()

    asyncio.run(run_backtest(args.strategy, args.symbols, args.limit, args.mode))

if __name__ == "__main__":
    main()
