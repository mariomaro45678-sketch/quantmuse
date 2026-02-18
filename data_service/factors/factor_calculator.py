import pandas as pd
import numpy as np
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

class FactorCalculator:
    """
    Vectorized engine for computing trading factors from OHLCV and Perpetual data.
    """

    async def calculate(self, df: pd.DataFrame, symbol: str, fetcher: Optional[Any] = None) -> Dict[str, float]:
        """
        Main entry point. Computes all required factors for a given symbol and candle DataFrame.
        """
        if df is None or df.empty:
            return {}

        df = df.sort_index()
        factors = {}

        # 1. Technical Factors (Vectorized)
        factors.update(self._compute_momentum(df))
        factors.update(self._compute_technical_indicators(df))
        factors.update(self._compute_volume_factors(df))
        
        # 2. Perpetual Factors (Async I/O)
        if fetcher and hasattr(fetcher, 'get_market_data'):
            perp_factors = await self._compute_perpetual_factors(symbol, fetcher)
            factors.update(perp_factors)

        return factors

    def _compute_momentum(self, df: pd.DataFrame) -> Dict[str, float]:
        """Vectorized momentum calculations."""
        res = {}
        # Assuming 1h candles as baseline for 'periods'
        # 1h: 1, 4h: 4, 1d: 24, 7d: 168
        intervals = {'momentum_1h': 1, 'momentum_4h': 4, 'momentum_1d': 24, 'momentum_7d': 168}
        
        for name, n in intervals.items():
            if len(df) > n:
                val = (df['close'].iloc[-1] / df['close'].iloc[-(n+1)]) - 1
                res[name] = float(val)
            else:
                res[name] = np.nan
        return res

    def _compute_technical_indicators(self, df: pd.DataFrame) -> Dict[str, float]:
        """Vectorized RSI, MACD, ATR, BB Width."""
        res = {}
        close = df['close']
        
        # --- RSI (14 period) ---
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        res['rsi_1h'] = float(rsi.iloc[-1]) if len(df) >= 14 else np.nan
        # Note: In a real app we'd compute RSI for multiple timeframes by resampling or fetching.
        # For this component, we provide the 1h/baseline.
        res['rsi_4h'] = res['rsi_1h'] # Placeholder or logic for resampled data
        res['rsi_1d'] = res['rsi_1h'] 

        # --- BB Width (20 period) ---
        sma20 = close.rolling(window=20).mean()
        std20 = close.rolling(window=20).std()
        upper = sma20 + (std20 * 2)
        lower = sma20 - (std20 * 2)
        bb_width = (upper - lower) / sma20
        res['bb_width_20'] = float(bb_width.iloc[-1]) if len(df) >= 20 else np.nan
        res['bb_width_1d'] = res['bb_width_20']

        # --- ATR (14 period) ---
        high = df['high']
        low = df['low']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean()
        res['atr_1h'] = float(atr.iloc[-1]) if len(df) >= 14 else np.nan
        res['atr_4h'] = res['atr_1h']

        # --- MACD (12, 26, 9) ---
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        res['macd_4h'] = float(macd_line.iloc[-1]) if len(df) >= 26 else np.nan
        res['macd_signal_4h'] = float(signal_line.iloc[-1]) if len(df) >= 26 else np.nan
        res['macd_1d'] = res['macd_4h']
        res['macd_signal_1d'] = res['macd_signal_4h']

        # --- ADX (14 period) ---
        if len(df) >= 28: # Need extra bars for smoothing stability
            res['adx'] = float(self._compute_adx(df))
        else:
            res['adx'] = np.nan

        return res

    def _compute_adx(self, df: pd.DataFrame, period: int = 14) -> float:
        """ Wilder's ADX calculation using Pandas vectorization. """
        high = df['high']
        low = df['low']
        close = df['close']
        
        up_move = high.diff().fillna(0)
        down_move = low.diff().fillna(0)
        
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
        
        tr = pd.concat([high - low, 
                        (high - close.shift(1)).abs(), 
                        (low - close.shift(1)).abs()], axis=1).max(axis=1)
        
        # Wilder's smoothing (simplified with rolling mean for this MVP)
        tr_smooth = tr.rolling(window=period).mean()
        
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / tr_smooth)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / tr_smooth)
        
        sum_di = plus_di + minus_di
        # Handle division by zero
        dx = 100 * (plus_di - minus_di).abs() / sum_di.replace(0, np.nan)
        dx = dx.fillna(0)
        
        adx = dx.rolling(window=period).mean()
        
        return float(adx.iloc[-1]) if not adx.empty and not np.isnan(adx.iloc[-1]) else 0.0

    def _compute_volume_factors(self, df: pd.DataFrame) -> Dict[str, float]:
        """Vectorized volume indicators."""
        res = {}
        vol = df['volume']
        
        # Volume Ratio (current / 20-period mean)
        if len(df) >= 20:
            mean_vol = vol.rolling(window=20).mean()
            mean_vol_val = mean_vol.iloc[-1]
            if mean_vol_val > 0:
                res['volume_ratio_1h'] = float(vol.iloc[-1] / mean_vol_val)
            else:
                res['volume_ratio_1h'] = 1.0 # Default if mean volume is zero
        else:
            res['volume_ratio_1h'] = np.nan
        res['volume_ratio_4h'] = res['volume_ratio_1h']

        # Volume-Weighted Return (1d = 24h)
        # sum(close * volume) / sum(volume) over window
        if len(df) >= 24:
            window = df.tail(24)
            total_vol = window['volume'].sum()
            if total_vol > 0:
                vwap = (window['close'] * window['volume']).sum() / total_vol
                res['vwap_return_1d'] = float((df['close'].iloc[-1] / vwap) - 1)
            else:
                res['vwap_return_1d'] = 0.0
        else:
            res['vwap_return_1d'] = np.nan

        return res

    async def _compute_perpetual_factors(self, symbol: str, fetcher: Any) -> Dict[str, float]:
        """Fetch and compute funding and OI metrics."""
        res = {
            'funding_rate_level': np.nan,
            'funding_momentum': np.nan,
            'open_interest_change': np.nan
        }
        
        try:
            # 1. Current Funding and OI from market data
            mdata = await fetcher.get_market_data(symbol)
            if mdata:
                res['open_interest_change'] = mdata.open_interest  # Just the level for now if change isn't possible
                
            # 2. Funding history
            funding_hist = await fetcher.get_funding_history(symbol, days=1)
            if funding_hist:
                latest_funding = funding_hist[0].rate
                res['funding_rate_level'] = latest_funding
                if len(funding_hist) > 24:
                    res['funding_momentum'] = latest_funding - funding_hist[-1].rate
                    
        except Exception as e:
            logger.debug(f"Could not compute perp factors for {symbol}: {e}")
            
        return res
