"""
Hyperliquid helper utilities.
Common functions and constants used across the system.
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


# Timeframe conversions
TIMEFRAME_TO_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def validate_timeframe(timeframe: str) -> bool:
    """Validate that a timeframe string is supported."""
    return timeframe in TIMEFRAME_TO_SECONDS


def timeframe_to_seconds(timeframe: str) -> int:
    """Convert timeframe string to seconds."""
    if not validate_timeframe(timeframe):
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return TIMEFRAME_TO_SECONDS[timeframe]


def format_symbol(symbol: str) -> str:
    """Format symbol to standard uppercase format."""
    return symbol.upper().strip()


def calculate_position_value(size: float, price: float) -> float:
    """Calculate position value in USD."""
    return abs(size) * price


def calculate_leverage(position_value: float, collateral: float) -> float:
    """Calculate effective leverage."""
    if collateral <= 0:
        return 0.0
    return position_value / collateral


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safely divide two numbers, returning default if denominator is zero."""
    if denominator == 0:
        return default
    return numerator / denominator
