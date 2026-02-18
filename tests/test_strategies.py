import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

from data_service.strategies.strategy_base import Signal
from data_service.strategies.momentum_perpetuals import MomentumPerpetuals
from data_service.strategies.mean_reversion_metals import MeanReversionMetals
from data_service.strategies.sentiment_driven import SentimentDriven

@pytest.fixture
def mock_market_data():
    """Generate 200 candles of mock market data."""
    limit = 200
    df = pd.DataFrame({
        'open': np.random.uniform(1900, 2000, limit),
        'high': np.random.uniform(2000, 2100, limit),
        'low': np.random.uniform(1800, 1900, limit),
        'close': np.random.uniform(1900, 2000, limit),
        'volume': np.random.uniform(1000, 5000, limit)
    })
    df.index = pd.date_range(start='2024-01-01', periods=limit, freq='h')
    return df

@pytest.fixture
def mock_factors():
    return {
        'fetcher': MagicMock()
    }

class TestMomentumPerpetuals:
    @pytest.mark.asyncio
    @patch('data_service.factors.factor_calculator.FactorCalculator.calculate', new_callable=AsyncMock)
    async def test_signal_generation_bullish(self, mock_calc, mock_market_data, mock_factors):
        # Mocking 3/3 TF agreement
        mock_calc.return_value = {
            'momentum_1h': 0.01,
            'momentum_4h': 0.02,
            'momentum_1d': 0.05,
            'adx': 30,
            'volume_ratio_4h': 1.2,
            'funding_rate_level': 0.0001
        }
        
        strat = MomentumPerpetuals()
        signals = await strat.calculate_signals({'XAU': mock_market_data}, mock_factors)
        
        sig = signals['XAU']
        assert sig.direction == 'long'
        assert sig.confidence >= 0.8
        assert "Bullish 3/3 TFs" in sig.rationale

    @pytest.mark.asyncio
    @patch('data_service.factors.factor_calculator.FactorCalculator.calculate', new_callable=AsyncMock)
    async def test_funding_filter_blocks_long(self, mock_calc, mock_market_data, mock_factors):
        # Bullish momentum but EXTREME funding
        mock_calc.return_value = {
            'momentum_1h': 0.01,
            'momentum_4h': 0.02,
            'momentum_1d': 0.05,
            'adx': 30,
            'volume_ratio_4h': 1.2,
            'funding_rate_level': 0.001  # Above 0.0005 threshold
        }
        
        strat = MomentumPerpetuals()
        signals = await strat.calculate_signals({'XAU': mock_market_data}, mock_factors)
        
        assert signals['XAU'].direction == 'flat'
        assert "high funding" in signals['XAU'].rationale

    def test_position_sizing(self):
        strat = MomentumPerpetuals()
        # Manually set config for testing
        strat.config['base_position_size'] = 0.1
        strat.config['max_total_exposure'] = 0.5
        strat.base_position_size = 0.1  # Also set the instance attr if it exists
        
        signals = {
            'XAU': Signal('XAU', 'long', 0.8, 'test'),
            'ETH': Signal('ETH', 'short', 0.6, 'test')
        }
        sizes = strat.size_positions(signals, None)
        
        assert sizes['XAU'] == pytest.approx(0.1 * 0.8)
        assert sizes['ETH'] == pytest.approx(-0.1 * 0.6)

    @pytest.mark.asyncio
    @patch('data_service.factors.factor_calculator.FactorCalculator.calculate', new_callable=AsyncMock)
    async def test_fuzzed_input_no_crash(self, mock_calc, mock_factors):
        """Fuzz test with random factor values to ensure no crashes."""
        strat = MomentumPerpetuals()
        for i in range(100):
            mock_calc.return_value = {
                'momentum_1h': np.random.normal(0, 0.1),
                'momentum_4h': np.random.normal(0, 0.1),
                'momentum_1d': np.random.normal(0, 0.1),
                'adx': np.random.uniform(0, 60),
                'volume_ratio_4h': np.random.uniform(0, 5),
                'funding_rate_level': np.random.normal(0, 0.001)
            }
            # Simple mock df
            df = pd.DataFrame({'close': [2000], 'volume': [1000]})
            df.index = [datetime.now()]
            
            try:
                await strat.calculate_signals({'XAU': df}, mock_factors)
            except Exception as e:
                pytest.fail(f"MomentumPerpetuals crashed on fuzzed input {i}: {e}")

class TestMeanReversionMetals:
    @pytest.mark.asyncio
    @patch('data_service.factors.factor_calculator.FactorCalculator.calculate', new_callable=AsyncMock)
    async def test_metals_only_guard(self, mock_calc, mock_market_data, mock_factors):
        strat = MeanReversionMetals()
        signals = await strat.calculate_signals({'TSLA': mock_market_data}, mock_factors)
        
        assert signals['TSLA'].direction == 'flat'
        assert "Non-metal asset excluded" in signals['TSLA'].rationale

    @pytest.mark.asyncio
    @patch('data_service.factors.factor_calculator.FactorCalculator.calculate', new_callable=AsyncMock)
    @patch('data_service.factors.metals_factors.MetalsFactors.calculate')
    async def test_signal_oversold(self, mock_metals, mock_calc, mock_market_data, mock_factors):
        # Mock factors for oversold
        mock_calc.return_value = {
            'rsi_1d': 20,
            'adx': 20,
        }
        mock_metals.return_value = {'gold_silver_ratio_zscore': 0}
        
        # Override market data to be below lower BB
        # SMA(20) ~ 1950, STD ~ 10 -> Lower BB ~ 1930
        mock_market_data['close'].iloc[-20:] = 1950
        mock_market_data['close'].iloc[-1] = 1900 
        
        strat = MeanReversionMetals()
        signals = await strat.calculate_signals({'XAU': mock_market_data}, mock_factors)
        
        assert signals['XAU'].direction == 'long'
        assert "Oversold" in signals['XAU'].rationale

    @pytest.mark.asyncio
    @patch('data_service.factors.factor_calculator.FactorCalculator.calculate', new_callable=AsyncMock)
    @patch('data_service.factors.metals_factors.MetalsFactors.calculate')
    async def test_fuzzed_input_no_crash(self, mock_metals, mock_calc, mock_factors):
        strat = MeanReversionMetals()
        for i in range(100):
            mock_calc.return_value = {
                'rsi_1d': np.random.uniform(0, 100),
                'adx': np.random.uniform(0, 60),
            }
            mock_metals.return_value = {'gold_silver_ratio_zscore': np.random.normal(0, 3)}
            df = pd.DataFrame({'close': [2000], 'high': [2100], 'low': [1900], 'volume': [1000]})
            df.index = [datetime.now()]
            
            try:
                await strat.calculate_signals({'XAU': df}, mock_factors)
            except Exception as e:
                pytest.fail(f"MeanReversionMetals crashed on fuzzed input {i}: {e}")

class TestSentimentDriven:
    @pytest.mark.asyncio
    @patch('data_service.factors.factor_calculator.FactorCalculator.calculate', new_callable=AsyncMock)
    @patch('data_service.ai.sentiment_factor.SentimentFactor.get_factors')
    async def test_sentiment_momentum_signal(self, mock_sf, mock_calc, mock_market_data, mock_factors):
        mock_calc.return_value = {'volume_ratio_1h': 1.2}
        mock_sf.return_value = {
            'sentiment_momentum': 0.4, # Bullish cross
            'sentiment_variance': 0.1
        }
        
        strat = SentimentDriven()
        signals = await strat.calculate_signals({'XAU': mock_market_data}, mock_factors)
        
        assert signals['XAU'].direction == 'long'
        assert "Bullish momentum" in signals['XAU'].rationale

    def test_signal_decay(self):
        strat = SentimentDriven()
        
        # 0.5h old -> Full weight
        assert strat.check_time_decay(0.5) == 1.0
        # 3.0h old -> Decay between 1.0 and 0.5
        assert 0.5 < strat.check_time_decay(3.0) < 1.0
        # 5.0h old -> Expired
        assert strat.check_time_decay(5.0) == 0.0

    @pytest.mark.asyncio
    @patch('data_service.factors.factor_calculator.FactorCalculator.calculate', new_callable=AsyncMock)
    @patch('data_service.ai.sentiment_factor.SentimentFactor.get_factors')
    async def test_low_volume_rejection(self, mock_sf, mock_calc, mock_market_data, mock_factors):
        mock_calc.return_value = {'volume_ratio_1h': 0.5} # Low volume
        mock_sf.return_value = {
            'sentiment_momentum': 0.5,
            'sentiment_variance': 0.1
        }
        
        strat = SentimentDriven()
        signals = await strat.calculate_signals({'XAU': mock_market_data}, mock_factors)
        
        assert signals['XAU'].direction == 'flat'
        assert "low volume" in signals['XAU'].rationale

    @pytest.mark.asyncio
    @patch('data_service.factors.factor_calculator.FactorCalculator.calculate', new_callable=AsyncMock)
    @patch('data_service.ai.sentiment_factor.SentimentFactor.get_factors')
    async def test_fuzzed_input_no_crash(self, mock_sf, mock_calc, mock_factors):
        strat = SentimentDriven()
        for i in range(100):
            mock_calc.return_value = {'volume_ratio_1h': np.random.uniform(0, 5)}
            mock_sf.return_value = {
                'sentiment_momentum': np.random.normal(0, 1),
                'sentiment_variance': np.random.uniform(0, 1)
            }
            df = pd.DataFrame({'close': [2000], 'volume': [1000]})
            df.index = [datetime.now()]
            
            try:
                await strat.calculate_signals({'XAU': df}, mock_factors)
            except Exception as e:
                pytest.fail(f"SentimentDriven crashed on fuzzed input {i}: {e}")
