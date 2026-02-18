
import unittest
from unittest.mock import MagicMock
from data_service.strategies.momentum_perpetuals import MomentumPerpetuals, Signal
from data_service.utils.config_loader import ConfigLoader

class TestAdaptiveSizing(unittest.TestCase):
    def setUp(self):
        # Initialize strategy
        self.strategy = MomentumPerpetuals()
        
        # Override config for deterministic testing
        self.strategy.config['position_size_method'] = 'volatility_scaled'
        self.strategy.config['risk_per_trade'] = 0.01  # 1% risk
        self.strategy.config['stop_loss_atr_multiplier'] = 2.0
        self.strategy.config['max_position_size'] = 1.0
        self.strategy.config['max_total_exposure'] = 5.0
        self.strategy.base_position_size = 0.10

    def test_volatility_scaling(self):
        """Verify that size decreases as volatility increases."""
        
        # Setup: Symbol A (Low Vol), Symbol B (High Vol)
        self.strategy.latest_atr = {
            'LOW_VOL': 0.01,  # 1% ATR
            'HIGH_VOL': 0.02, # 2% ATR
            'TINY_VOL': 0.005 # 0.5% ATR
        }
        
        signals = {
            'LOW_VOL': Signal('LOW_VOL', 'long', 1.0, "Test"),
            'HIGH_VOL': Signal('HIGH_VOL', 'long', 1.0, "Test"),
            'TINY_VOL': Signal('TINY_VOL', 'long', 1.0, "Test")
        }
        
        # Execute
        positions = self.strategy.size_positions(signals, {})
        
        # Assertions
        # 1. Low Vol (1% ATR): Size = 1% / (1% * 2) = 0.5
        self.assertAlmostEqual(positions['LOW_VOL'], 0.5, places=2)
        
        # 2. High Vol (2% ATR): Size = 1% / (2% * 2) = 0.25
        self.assertAlmostEqual(positions['HIGH_VOL'], 0.25, places=2)
        
        # 3. Tiny Vol (0.5% ATR): Size = 1% / (0.5% * 2) = 1.0
        self.assertAlmostEqual(positions['TINY_VOL'], 1.0, places=2)
        
        print("\n✅ Volatility Sizing Test Passed:")
        print(f"  Low Vol (1% ATR)  -> Size: {positions['LOW_VOL']:.2f}")
        print(f"  High Vol (2% ATR) -> Size: {positions['HIGH_VOL']:.2f}")

    def test_confidence_scaling(self):
        """Verify that size scales with confidence."""
        self.strategy.latest_atr = {'TEST': 0.01} # 1% ATR
        
        # Confidence 0.5
        signals = {'TEST': Signal('TEST', 'long', 0.5, "Test")}
        
        positions = self.strategy.size_positions(signals, {})
        
        # Expected: Base (0.5) * Confidence (0.5) = 0.25
        self.assertAlmostEqual(positions['TEST'], 0.25, places=2)
        print(f"✅ Confidence Scaling Passed: 50% conf -> 50% size")

if __name__ == '__main__':
    unittest.main()
