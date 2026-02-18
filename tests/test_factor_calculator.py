import pytest
import pandas as pd
import numpy as np
import math
from data_service.factors.factor_calculator import FactorCalculator

@pytest.fixture
def sample_data():
    """Generate 200 candles of trending data."""
    np.random.seed(42)
    limit = 200
    base_px = 100.0
    rets = np.random.normal(0, 0.01, limit)
    prices = base_px * np.exp(np.cumsum(rets))
    
    df = pd.DataFrame({
        'open': prices * 0.999,
        'high': prices * 1.005,
        'low': prices * 0.995,
        'close': prices,
        'volume': np.random.uniform(1000, 5000, limit)
    })
    return df

@pytest.mark.asyncio
async def test_momentum_calc(sample_data):
    fc = FactorCalculator()
    factors = await fc.calculate(sample_data, "TEST")
    
    # 手算 momentum_1h
    expected_1h = (sample_data['close'].iloc[-1] / sample_data['close'].iloc[-2]) - 1
    assert math.isclose(factors['momentum_1h'], expected_1h, rel_tol=1e-5)
    
    # 手算 momentum_4h
    expected_4h = (sample_data['close'].iloc[-1] / sample_data['close'].iloc[-5]) - 1
    assert math.isclose(factors['momentum_4h'], expected_4h, rel_tol=1e-5)

@pytest.mark.asyncio
async def test_rsi_range(sample_data):
    fc = FactorCalculator()
    factors = await fc.calculate(sample_data, "TEST")
    assert 0 <= factors['rsi_1h'] <= 100

@pytest.mark.asyncio
async def test_nan_contract():
    fc = FactorCalculator()
    # Only 5 candles
    short_df = pd.DataFrame({
        'close': [100, 101, 102, 103, 104],
        'high': [105]*5, 'low': [95]*5, 'volume': [1000]*5
    })
    factors = await fc.calculate(short_df, "TEST")
    
    assert math.isnan(factors['momentum_1d']) # need 24
    assert math.isnan(factors['rsi_1h'])      # need 14
    assert math.isnan(factors['bb_width_20']) # need 20

def test_vectorization_grep():
    """Ensure no row iteration in calculator."""
    import subprocess
    cmd = "grep -n 'for.*iterrows\\|\\.apply(lambda\\|for i in range(len' data_service/factors/factor_calculator.py"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.stdout == "", f"Found non-vectorized patterns:\n{result.stdout}"
