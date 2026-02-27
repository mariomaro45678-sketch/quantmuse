import asyncio
import argparse
import logging
import sys
import signal
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from data_service.utils.logging_config import setup_logging
from data_service.utils.config_loader import get_config
from data_service.utils.health_check import get_health
from data_service.storage.database_manager import DatabaseManager
from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher
from data_service.strategies.strategy_base import STRATEGY_REGISTRY
from data_service.strategies.momentum_perpetuals import MomentumPerpetuals
from data_service.strategies.mean_reversion_metals import MeanReversionMetals
from data_service.strategies.sentiment_driven import SentimentDriven

from data_service.risk.risk_manager import RiskManager
from data_service.risk.position_sizer import PositionSizer
from data_service.executors.order_manager import OrderManager
from data_service.executors.hyperliquid_executor import HyperliquidExecutor
from data_service.ai.news_processor import NewsProcessor

logger = logging.getLogger("TradingSystem")

class TradingEngine:
    def __init__(self, mode: str, strategy_name: str, assets: list):
        self.mode = mode
        self.strategy_name = strategy_name
        self.assets = assets
        self.running = False
        
        # Initialize Core Components
        self.db = DatabaseManager()
        self.fetcher = HyperliquidFetcher(mode=mode)
        self.executor = HyperliquidExecutor(mode=mode)
        
        # Strategy selection
        if strategy_name not in STRATEGY_REGISTRY:
            # Fallback to momentum if not found, for mock runs
            logger.warning(f"Strategy {strategy_name} not found, defaulting to momentum_perpetuals")
            self.strategy_name = "momentum_perpetuals"
            
        self.strategy = STRATEGY_REGISTRY[self.strategy_name]()
        
        self.risk_mgr = RiskManager(db_manager=self.db)
        self.order_mgr = OrderManager(executor=self.executor, risk_manager=self.risk_mgr)
        self.pos_sizer = PositionSizer(risk_manager=self.risk_mgr)
        self.news_processor = NewsProcessor(mode=mode)
        
        # Set initial risk state
        self.risk_mgr.set_portfolio(equity=100_000, open_positions=[])
        
    async def run(self):
        """Main execution loop."""
        self.running = True
        logger.info(f"🚀 Trading Engine started in {self.mode} mode")
        logger.info(f"Strategy: {self.strategy_name} | Assets: {self.assets}")
        
        # Start risk snapshot loop
        self.risk_mgr.start_snapshot_loop()
        
        while self.running:
            try:
                # 1. Market Data Fetching
                market_data = {}
                for sym in self.assets:
                    try:
                        df = await self.fetcher.get_candles(sym, timeframe='1h', limit=100)
                        market_data[sym] = df
                    except Exception as e:
                        logger.error(f"Error fetching data for {sym}: {e}")
                
                if not market_data:
                    await asyncio.sleep(5)
                    continue

                # 2. Strategy Signal Calculation
                factors = {'fetcher': self.fetcher}
                try:
                    signals = await self.strategy.calculate_signals(market_data, factors)
                except Exception as e:
                    logger.error(f"Error calculating signals: {e}")
                    await asyncio.sleep(10)
                    continue
                
                # 3. Position Sizing & Execution
                target_positions = self.strategy.size_positions(signals, None)

                # Get current positions and equity
                current_positions = {}
                try:
                    positions = await self.executor.get_positions()
                    for pos in positions:
                        coin = pos.symbol.split(':')[-1] if ':' in pos.symbol else pos.symbol
                        current_positions[coin] = pos.size
                        
                    # Fetch User State to update Equity
                    user_state = await self.executor.get_user_state()
                    self.risk_mgr.set_portfolio(
                        equity=user_state.total_equity if hasattr(user_state, 'total_equity') else getattr(user_state, 'equity', 100_000.0),
                        open_positions=[{'symbol': p.symbol, 'notional': p.size * p.entry_price} for p in positions]
                    )
                except Exception as e:
                    logger.debug(f"Position/State fetch error: {e}")

                for sym, target_pct in target_positions.items():
                    current_size = current_positions.get(sym, 0)

                    # Skip if no action needed (no position and no signal)
                    if target_pct == 0 and abs(current_size) < 1e-8:
                        continue

                    try:
                        md = await self.fetcher.get_market_data(sym)
                        px = md.mid_price

                        target_size = target_pct * self.risk_mgr.equity / px
                        delta_size = target_size - current_size

                        # Determine if closing
                        is_closing = False
                        if current_size > 0 and delta_size < 0:
                            is_closing = True
                        elif current_size < 0 and delta_size > 0:
                            is_closing = True

                        delta_notional = abs(delta_size * px)
                        min_delta = 2.0 if is_closing else 5.0
                        if delta_notional < min_delta:
                            continue

                        side = "buy" if delta_size > 0 else "sell"
                        raw_size = abs(delta_size)

                        final_size = self.pos_sizer.apply_constraints(
                            symbol=sym,
                            raw_size=raw_size,
                            leverage=3.0,
                            price=px,
                            min_order_size=0.001,
                            is_closing=is_closing,
                            side=side,
                            strategy_name=self.strategy_name
                        )

                        if final_size > 0:
                            tag = " [CLOSE]" if is_closing else ""
                            logger.info(f"Risk Check Passed{tag}: {sym} {side} size={final_size:.4f} @ {px}")

                            res = await self.order_mgr.create_order(
                                symbol=sym,
                                side=side,
                                sz=final_size,
                                px=px,
                                strategy_name=self.strategy_name,
                                is_closing=is_closing
                            )
                            if not res.success:
                                logger.error(f"Order Execution Failed: {res.error}")
                            else:
                                logger.info(f"Order Successful: {res.order_id}")
                                get_health().record_order()

                    except Exception as e:
                        logger.error(f"Error processing strategy logic for {sym}: {e}")

                # 4. News Processing
                if self.mode == "mock":
                    # In mock mode, we just simulate news processing
                    # self.news_processor.fetch_news(self.assets)
                    pass

                # Update health status
                get_health().record_api_call()
                
                # Wait for next cycle (shortened for mock/test, usually matching timeframe)
                interval = 30 if self.mode == "mock" else 60
                await asyncio.sleep(interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"CRITICAL ERROR in main loop: {e}", exc_info=True)
                await asyncio.sleep(10) # Cooling off after error

    def stop(self):
        """Graceful shutdown."""
        self.running = False
        self.risk_mgr.stop_snapshot_loop()
        logger.info("🛑 Trading Engine shutdown requested")


async def main_async():
    parser = argparse.ArgumentParser(description="Hyperliquid DEX Trading System")
    parser.add_argument("--mode", choices=["mock", "testnet", "paper-trading", "live"], default="mock")
    parser.add_argument("--strategy", default="momentum_perpetuals")
    parser.add_argument("--symbols", "--assets", default="XAU,XAG,BTC,ETH", 
                       help="Comma-separated list of symbols to trade")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    # Initialize logging with secret redaction (setup_logging from logging_config handles this)
    setup_logging(level=args.log_level)
    
    # Support both --symbols and --assets for backwards compatibility
    assets = (args.symbols if hasattr(args, 'symbols') else args.assets).split(",")
    
    logger.info(f"Starting Trading Engine")
    logger.info(f"Mode: {args.mode}")
    logger.info(f"Strategy: {args.strategy}")
    logger.info(f"Assets: {', '.join(assets)}")
    
    engine = TradingEngine(args.mode, args.strategy, assets)

    # Handle termination signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: engine.stop())

    try:
        await engine.run()
    except Exception as e:
        logger.critical(f"Fatal crash: {e}")
    finally:
        engine.stop()
        logger.info("System exited cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        sys.stderr.write(f"Failed to start system: {e}\n")
        sys.exit(1)
