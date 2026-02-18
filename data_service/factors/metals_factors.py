import pandas as pd
import numpy as np
import logging
from typing import Dict, Any, Optional
from data_service.storage.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

class MetalsFactors:
    """
    Computes cross-asset metals factors such as ratios and correlations.
    """
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()

    def calculate(self, candles: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        """
        Compute metals specific factors from a dictionary of DataFrames.
        Expected keys: 'XAU', 'XAG', 'HG', 'PLAT'.
        """
        res = {}
        
        # 1. Gold/Silver Ratio
        if 'XAU' in candles and 'XAG' in candles:
            xau_close = candles['XAU']['close']
            xag_close = candles['XAG']['close']
            
            # Use the latest aligned index if possible, otherwise just latest from each
            current_ratio = xau_close.iloc[-1] / xag_close.iloc[-1]
            res['gold_silver_ratio'] = float(current_ratio)
            
            # Z-Score (30d)
            # We assume '1d' candles are passed or we use the last 30 entries
            if len(xau_close) >= 30 and len(xag_close) >= 30:
                historical_ratios = xau_close.tail(30).values / xag_close.tail(30).values
                mean = np.mean(historical_ratios)
                std = np.std(historical_ratios)
                res['gold_silver_ratio_zscore'] = float((current_ratio - mean) / std) if std > 0 else 0.0
            else:
                res['gold_silver_ratio_zscore'] = np.nan

        # 2. Copper/Gold Ratio (Risk Indicator)
        if 'HG' in candles and 'XAU' in candles:
            res['copper_gold_ratio'] = float(candles['HG']['close'].iloc[-1] / candles['XAU']['close'].iloc[-1])

        # 3. Industrial Basket Momentum (Equal-weight HG + Platinum)
        if 'HG' in candles and 'PLAT' in candles:
            hg_mom = (candles['HG']['close'].iloc[-1] / candles['HG']['close'].iloc[-2]) - 1 if len(candles['HG']) > 1 else 0
            pt_mom = (candles['PLAT']['close'].iloc[-1] / candles['PLAT']['close'].iloc[-2]) - 1 if len(candles['PLAT']) > 1 else 0
            res['industrial_basket_momentum'] = float((hg_mom + pt_mom) / 2)

        # 4. Persistence
        if res:
            self.db.save_metals_snapshot(res)
            
        return res
