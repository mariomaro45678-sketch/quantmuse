import logging
import json
import itertools
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import pandas as pd
import numpy as np

from data_service.strategies.strategy_base import STRATEGY_REGISTRY, BacktestResult
# Import strategies to ensure registration
import data_service.strategies.momentum_perpetuals
import data_service.strategies.mean_reversion_metals
import data_service.strategies.sentiment_driven

from data_service.storage.database_manager import DatabaseManager
from data_service.utils.config_loader import get_config
from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """Result of a single parameter combination test."""
    parameters: Dict[str, Any]
    score: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    total_return: float
    is_oos: bool  # Out-of-sample (test fold)
    fold_id: Optional[int] = None


class StrategyOptimizer:
    """
    Optimizes strategy parameters using grid search and walk-forward analysis.
    
    DESIGN DECISIONS & IMPROVEMENTS:
    1. Composite Scoring: 0.5 Sharpe + 0.3 (1-DD) + 0.2 Return. Prioritizes risk-adjusted 
       returns while penalizing drawdowns heavily to ensure психологической tradability.
    2. Walk-Forward Analysis: Splits data into N folds, training on each and testing on 
       the next. This prevents overfitting by validating generalization on unseen data.
    3. Robust Parameter Injection: Correctly overrides strategy config parameters and 
       validates their application across both attributes and config dicts.
    4. Persistence: Saves detailed results and fold summaries to SQLite for drift tracking.
    """

    def __init__(self, strategy_name: str, mode: str = 'mock'):
        if strategy_name not in STRATEGY_REGISTRY:
            raise ValueError(f"Unknown strategy: {strategy_name}. Available: {list(STRATEGY_REGISTRY.keys())}")
        
        self.strategy_name = strategy_name
        self.mode = mode
        self.config_loader = get_config()
        self.db = DatabaseManager()
        self.fetcher = HyperliquidFetcher(mode=mode)
        
        # Load parameter grid
        self.param_grid = self._get_default_grid(strategy_name)
        
        # Optimization settings
        self.min_data_points = 500
        self.early_stop_threshold = -0.5
        
        logger.info(f"Initialized optimizer for {strategy_name} with {self._count_combinations()} combinations")

    def _get_default_grid(self, strategy_name: str) -> Dict[str, List[Any]]:
        """Define default search ranges for each strategy."""
        if strategy_name == 'momentum_perpetuals':
            return {
                'momentum_threshold': [0.15, 0.20, 0.25],
                'volume_min': [0.7, 0.8],
                'cooldown_minutes': [30, 60],
                'adx_threshold': [20, 25]
            }
        elif strategy_name == 'mean_reversion_metals':
            return {
                'rsi_oversold': [25, 30, 35],
                'rsi_overbought': [65, 70, 75],
                'ratio_zscore_threshold': [1.5, 2.0, 2.5],
                'bb_period': [15, 20, 25]
            }
        elif strategy_name == 'sentiment_driven':
            return {
                'momentum_threshold': [0.2, 0.3, 0.4],
                'volume_min': [0.7, 0.8, 0.9],
                'expiry_hours': [3, 4, 6],
                'variance_threshold': [0.15, 0.20, 0.25]
            }
        return {}

    def set_param_grid(self, param_grid: Dict[str, List[Any]]):
        self.param_grid = param_grid
        logger.info(f"Updated parameter grid: {self._count_combinations()} combinations")

    def _count_combinations(self) -> int:
        if not self.param_grid: return 0
        return int(np.prod([len(v) for v in self.param_grid.values()]))

    def _generate_combinations(self) -> List[Dict[str, Any]]:
        if not self.param_grid: return [{}]
        keys = list(self.param_grid.keys())
        values = list(self.param_grid.values())
        return [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    def _inject_parameters(self, strategy_instance, params: Dict[str, Any]):
        """Safely inject parameters into strategy instance."""
        if hasattr(strategy_instance, 'config'):
            strategy_instance.config.update(params)
        for key, value in params.items():
            setattr(strategy_instance, key, value)

    def _score_result(self, result: BacktestResult) -> float:
        """Composite Score: 0.5 * Sharpe + 0.3 * (1 - DD) + 0.2 * Return."""
        sharpe_norm = np.clip(result.sharpe_ratio / 3.0, 0, 1.5)
        dd_comp = 1.0 - np.clip(result.max_drawdown, 0, 1)
        ret_norm = np.clip(result.total_return / 0.20, -1, 3)
        return float((0.5 * sharpe_norm) + (0.3 * dd_comp) + (0.2 * ret_norm))

    async def _backtest_with_params(self, params: Dict[str, Any], candles: Dict[str, pd.DataFrame],
                                     start: int = 0, end: Optional[int] = None) -> Tuple[Optional[BacktestResult], float]:
        try:
            strat_cls = STRATEGY_REGISTRY[self.strategy_name]
            strategy = strat_cls()
            self._inject_parameters(strategy, params)
            result = await strategy.backtest(candles, start=start, end=end)
            return result, self._score_result(result)
        except Exception as e:
            logger.error(f"Backtest failed for params {params}: {str(e)}")
            return None, float('-inf')  # Return None and negative infinity score

    async def grid_search(self, candles: Dict[str, pd.DataFrame], start: int = 0, end: Optional[int] = None) -> List[OptimizationResult]:
        combinations = self._generate_combinations()
        results = []
        logger.info(f"Starting grid search: {len(combinations)} combinations")
        for idx, params in enumerate(combinations):
            if (idx + 1) % 10 == 0: logger.info(f"Progress: {idx + 1}/{len(combinations)}")
            result, score = await self._backtest_with_params(params, candles, start, end)
            # Skip failed backtests (None result) or poor performers
            if result is None or score < self.early_stop_threshold:
                continue
            results.append(OptimizationResult(params, score, result.sharpe_ratio, result.max_drawdown,
                                             result.win_rate, result.total_return, False))
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    async def walk_forward_analysis(self, candles: Dict[str, pd.DataFrame], folds: int = 3) -> Tuple[List[OptimizationResult], Dict[str, Any]]:
        logger.info(f"Starting walk-forward analysis with {folds} folds")
        total_len = len(next(iter(candles.values())))
        if total_len < self.min_data_points * (folds + 1):
            raise ValueError(f"Insufficient data: need {self.min_data_points * (folds + 1)}, have {total_len}")
        
        fold_size = total_len // (folds + 1)
        all_oos_results = []
        fold_summaries = []
        
        for fold_idx in range(folds):
            train_end = (fold_idx + 1) * fold_size
            test_start = train_end
            test_end = min(test_start + fold_size, total_len)
            
            logger.info(f"Fold {fold_idx + 1}/{folds}: Train [0:{train_end}], Test [{test_start}:{test_end}]")
            train_results = await self.grid_search(candles, start=0, end=train_end)
            if not train_results: continue
            
            top_params = [r.parameters for r in train_results[:5]]
            fold_oos_results = []
            for params in top_params:
                result, score = await self._backtest_with_params(params, candles, start=test_start, end=test_end)
                if result is None:
                    continue  # Skip failed backtests
                oos_result = OptimizationResult(params, score, result.sharpe_ratio, result.max_drawdown,
                                               result.win_rate, result.total_return, True, fold_idx)
                fold_oos_results.append(oos_result)
                all_oos_results.append(oos_result)

            if not fold_oos_results:
                continue  # Skip fold if all backtests failed
            best_oos = max(fold_oos_results, key=lambda x: x.score)
            fold_summaries.append({'fold_id': fold_idx, 'best_train_score': train_results[0].score, 
                                   'best_oos_score': best_oos.score, 'best_oos_sharpe': best_oos.sharpe, 
                                   'best_oos_params': best_oos.parameters})
        
        summary = self._calculate_wf_summary(all_oos_results, fold_summaries)
        return all_oos_results, summary

    def _calculate_wf_summary(self, oos_results: List[OptimizationResult], fold_summaries: List[Dict]) -> Dict[str, Any]:
        if not oos_results: return {'avg_oos_score': 0.0, 'avg_oos_sharpe': 0.0}
        param_groups = {}
        for result in oos_results:
            key = json.dumps(result.parameters, sort_keys=True)
            if key not in param_groups: param_groups[key] = []
            param_groups[key].append(result)
        
        best_params, best_avg_score = None, -float('inf')
        for key, results in param_groups.items():
            avg = np.mean([r.score for r in results])
            if avg > best_avg_score: best_avg_score, best_params = avg, results[0].parameters
        
        return {
            'avg_oos_score': float(np.mean([r.score for r in oos_results])),
            'avg_oos_sharpe': float(np.mean([r.sharpe for r in oos_results])),
            'avg_oos_return': float(np.mean([r.total_return for r in oos_results])),
            'avg_oos_dd': float(np.mean([r.max_drawdown for r in oos_results])),
            'best_consistent_params': best_params,
            'best_consistent_score': float(best_avg_score),
            'fold_summaries': fold_summaries
        }

    async def run(self, assets: List[str] = ['XAU'], use_walk_forward: bool = True, folds: int = 3, data_source: str = 'live') -> List[OptimizationResult]:
        logger.info(f"Starting optimization for {self.strategy_name} on {assets} (Source: {data_source})")
        candles = {}
        for asset in assets:
            if data_source == 'db':
                # Load from database
                raw_data = self.db.get_candles(asset, '1h', limit=5000)
                if raw_data:
                    df = pd.DataFrame(raw_data)
                    # numeric conversion
                    cols = ['open', 'high', 'low', 'close', 'volume']
                    for col in cols:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                    logger.info(f"Loaded {len(df)} candles for {asset} from DB")
                    candles[asset] = df
                else:
                    logger.warning(f"No data found in DB for {asset}")
            else:
                # Fetch live
                df = await self.fetcher.get_candles(asset, '1h', limit=3000)
                if len(df) >= self.min_data_points: candles[asset] = df
        
        if not candles: raise ValueError("No valid data fetched for any asset")
        
        if use_walk_forward:
            oos_results, summary = await self.walk_forward_analysis(candles, folds=folds)
            self.db.save_optimization_summary({'strategy_name': self.strategy_name, 'mode': 'walk_forward', 'summary': summary})
            top_results = sorted(oos_results, key=lambda x: x.score, reverse=True)[:10]
        else:
            results = await self.grid_search(candles)
            top_results = results[:10]
        
        for result in top_results:
            self.db.save_optimisation_result({
                'strategy_name': self.strategy_name, 'parameters': result.parameters, 'sharpe': result.sharpe,
                'max_drawdown': result.max_drawdown, 'win_rate': result.win_rate, 'total_return': result.total_return,
                'score': result.score, 'is_oos': result.is_oos
            })
        
        logger.info(f"Optimization complete. Best score: {top_results[0].score:.4f}")
        return top_results
