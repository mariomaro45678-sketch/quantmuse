import asyncio
import argparse
import sys
import logging
import json
from data_service.strategies.strategy_optimizer import StrategyOptimizer, OptimizationResult

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('AutoOptimizer')

async def main():
    parser = argparse.ArgumentParser(description='Hyperliquid Strategy Auto-Optimizer')
    parser.add_argument('--strategy', required=True, help='Strategy name (e.g., momentum_perpetuals)')
    parser.add_argument('--assets', default='XAU', help='Comma-separated list of assets')
    parser.add_argument('--mode', default='db', choices=['live', 'db', 'mock'], help='Data source')
    parser.add_argument('--folds', type=int, default=3, help='Number of walk-forward folds')
    parser.add_argument('--no-wfa', action='store_true', help='Disable Walk-Forward Analysis (use Grid Search)')
    parser.add_argument('--days', type=int, default=180, help='Days of data to fetch (if live)')
    parser.add_argument('--dry-run', action='store_true', help='Run with minimal grid for testing')
    
    args = parser.parse_args()
    
    assets = args.assets.split(',')
    use_wfa = not args.no_wfa
    
    logger.info(f"Arguments: Strategy={args.strategy}, Assets={assets}, Mode={args.mode}, WFA={use_wfa}, DryRun={args.dry_run}")

    try:
        optimizer = StrategyOptimizer(strategy_name=args.strategy, mode=args.mode)
        
        if args.dry_run:
            logger.info("DRY RUN enabled: Overriding parameter grid with minimal test set")
            # Minimal grid for quick testing
            if args.strategy == 'momentum_perpetuals':
                optimizer.set_param_grid({
                    'momentum_threshold': [0.2],
                    'volume_min': [0.8],
                    'cooldown_minutes': [60],
                    'adx_threshold': [25]
                })
            # Add other strategies if needed
        
        # Run optimization
        results = await optimizer.run(
            assets=assets,
            use_walk_forward=use_wfa,
            folds=args.folds,
            data_source=args.mode
        )
        
        # Display Results
        print("\n" + "="*60)
        print(f"OPTIMIZATION RESULTS: {args.strategy}")
        print("="*60)
        
        best_result = results[0]
        
        print(f"Best Score:       {best_result.score:.4f}")
        print(f"Sharpe Ratio:     {best_result.sharpe:.4f}")
        print(f"Total Return:     {best_result.total_return:.2%}")
        print(f"Max Drawdown:     {best_result.max_drawdown:.2%}")
        print(f"Win Rate:         {best_result.win_rate:.1%}")
        print(f"Validation:       {'Out-of-Sample' if best_result.is_oos else 'In-Sample'}")
        print("-" * 30)
        print("Best Parameters:")
        print(json.dumps(best_result.parameters, indent=2))
        print("="*60)
        
        # Save best parameters to file
        output_file = f"optimized_{args.strategy}.json"
        with open(output_file, 'w') as f:
            json.dump(best_result.parameters, f, indent=4)
        print(f"\n✅ Optimized parameters saved to {output_file}")
        
    except Exception as e:
        logger.error(f"Optimization failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    asyncio.run(main())
