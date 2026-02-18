"""
Enhanced Scalper - Microstructure-based Scalping Strategy
=========================================================

This package provides advanced microstructure analysis for high-frequency trading:

Components:
- ultra_scalper_pro.py: Main strategy engine (standalone version)
- orderbook_analyzer.py: Order book imbalance and liquidity analysis
- volume_delta_analyzer.py: Volume delta and footprint analysis
- stop_hunt_detector.py: Liquidity sweep and stop hunt detection
- risk_manager_high_leverage.py: Standalone risk management (not used in integrated mode)

Integration:
- When used with QuantMuse, import via data_service.strategies.enhanced_scalper
- The integrated version uses system RiskManager, PositionSizer, and OrderManager
- Position conflict detection prevents trading same symbols as other strategies
"""

from .orderbook_analyzer import OrderBookMicrostructureAnalyzer, create_order_book_snapshot
from .volume_delta_analyzer import VolumeDeltaAnalyzer, TickData
from .stop_hunt_detector import StopHuntDetector
from .risk_manager_high_leverage import HighLeverageRiskManager
from .ultra_scalper_pro import HyperLiquidUltraScalper, MicrostructureSignal, TradeDirection

__all__ = [
    'OrderBookMicrostructureAnalyzer',
    'create_order_book_snapshot',
    'VolumeDeltaAnalyzer',
    'TickData',
    'StopHuntDetector',
    'HighLeverageRiskManager',
    'HyperLiquidUltraScalper',
    'MicrostructureSignal',
    'TradeDirection',
]

__version__ = '1.1.0'
