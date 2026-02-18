import logging
import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from data_service.utils.config_loader import get_config

logger = logging.getLogger(__name__)

@dataclass
class RankedAsset:
    symbol: str
    score: float
    factors: Dict[str, float]

class FactorScreener:
    """
    Ranks and screens assets based on quantitative factors and strategy weights.
    """
    def __init__(self, mode: str = "live"):
        self.config = get_config()
        self.mode = mode
        self.strategy_config = self.config.strategies.get('strategies', {})
        
        # Default filters from global settings or defaults
        self.min_oi = 100000.0
        self.max_funding = 0.001  # 0.1% per 8h

    def rank(self, assets_factors: Dict[str, Dict[str, float]], factor_name: str, ascending: bool = False) -> List[RankedAsset]:
        """
        Rank all assets by a single factor.
        """
        ranked = []
        for symbol, factors in assets_factors.items():
            val = factors.get(factor_name, -np.inf if not ascending else np.inf)
            ranked.append(RankedAsset(symbol=symbol, score=val, factors=factors))
            
        return sorted(ranked, key=lambda x: x.score, reverse=not ascending)

    def screen(self, strategy_name: str, assets_factors: Dict[str, Dict[str, float]]) -> List[RankedAsset]:
        """
        Rank assets based on a composite score defined for a strategy.
        """
        strat_conf = self.strategy_config.get(strategy_name, {})
        if not strat_conf:
            logger.warning(f"Strategy {strategy_name} not found in config. Returning empty.")
            return []

        # Weights are usually in config, if not, we use a default based on the strategy type
        # For this implementation, we look for 'factor_weights' in the strategy config.
        weights = strat_conf.get('factor_weights', self._get_default_weights(strategy_name))
        applicable_assets = strat_conf.get('applicable_assets', [])

        screened = []
        for symbol, factors in assets_factors.items():
            if applicable_assets and symbol not in applicable_assets:
                continue
                
            # Filter check
            if not self._passes_filters(symbol, factors):
                continue
                
            # Composite score (weighted average)
            score = 0.0
            total_weight = 0.0
            for f_name, weight in weights.items():
                val = factors.get(f_name)
                if val is not None and not np.isnan(val):
                    score += val * weight
                    total_weight += abs(weight)
            
            final_score = score / total_weight if total_weight > 0 else 0.0
            screened.append(RankedAsset(symbol=symbol, score=final_score, factors=factors))

        return sorted(screened, key=lambda x: x.score, reverse=True)

    def _passes_filters(self, symbol: str, factors: Dict[str, float]) -> bool:
        """Apply liquidity and funding filters."""
        # 1. Funding Rate Filter
        funding = factors.get('funding_rate_level', 0.0)
        if abs(funding) > self.max_funding:
            logger.debug(f"Asset {symbol} filtered out: Funding {funding} > {self.max_funding}")
            return False
            
        # 2. Open Interest Filter
        oi = factors.get('open_interest_level', factors.get('open_interest_change', 1e9)) # fallback to allow if not found
        if oi < self.min_oi:
            logger.debug(f"Asset {symbol} filtered out: OI {oi} < {self.min_oi}")
            return False
            
        return True

    def _get_default_weights(self, strategy_name: str) -> Dict[str, float]:
        """Return hardcoded weights if not in config."""
        if "momentum" in strategy_name:
            return {
                "momentum_1h": 0.2,
                "momentum_4h": 0.3,
                "momentum_1d": 0.5,
                "volume_ratio_1h": 0.2
            }
        elif "sentiment" in strategy_name:
            return {
                "sentiment_level": 0.7,
                "sentiment_momentum": 0.3
            }
        return {"momentum_1h": 1.0}
        
    def _inject_test_asset(self, symbol: str, funding_rate: float, open_interest: float):
        """Hidden helper for unit testing filters."""
        # This will be used by the test script provided in the spec
        pass
