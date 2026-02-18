"""
HIP-3 Symbol Mapping for Hyperliquid Builder-Deployed Perpetuals

Maps internal symbols (XAU, TSLA) to HIP-3 format (flx:GOLD, xyz:TSLA).

DEX Index Reference:
    0 = Main perps (BTC, ETH, etc.)
    1 = xyz DEX (stocks)
    2 = flx DEX (metals/commodities)
    3 = vntl DEX (private companies)
    4 = hyna DEX (crypto)
    5 = km DEX (indices)
    6 = abcd DEX
    7 = cash DEX (mixed)
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# HIP-3 Symbol Mapping: Internal -> (HIP-3 Name, DEX Index)
HIP3_MAPPING: Dict[str, Tuple[str, int]] = {
    # Metals via flx DEX (index 2)
    "XAU": ("flx:GOLD", 2),
    "GOLD": ("flx:GOLD", 2),
    "XAG": ("flx:SILVER", 2),
    "SILVER": ("flx:SILVER", 2),
    "HG": ("flx:COPPER", 2),
    "COPPER": ("flx:COPPER", 2),
    "CL": ("flx:OIL", 2),
    "OIL": ("flx:OIL", 2),

    # Stocks via xyz DEX (index 1)
    "TSLA": ("xyz:TSLA", 1),
    "NVDA": ("xyz:NVDA", 1),
    "META": ("xyz:META", 1),
    "AAPL": ("xyz:AAPL", 1),
    "MSFT": ("xyz:MSFT", 1),
    "GOOGL": ("xyz:GOOGL", 1),
    "AMZN": ("xyz:AMZN", 1),
    "AMD": ("xyz:AMD", 1),
    "COIN": ("xyz:COIN", 1),
    "INTC": ("xyz:INTC", 1),
    "NFLX": ("xyz:NFLX", 1),
    "MSTR": ("xyz:MSTR", 1),

    # Indices via km DEX (index 5)
    "SPX": ("km:US500", 5),
    "US500": ("km:US500", 5),
    "NDX": ("km:USTECH", 5),
    "USTECH": ("km:USTECH", 5),
}

# Reverse mapping: HIP-3 -> Internal
REVERSE_MAPPING: Dict[str, str] = {
    v[0]: k for k, v in HIP3_MAPPING.items()
}


def to_hip3_symbol(internal_symbol: str) -> str:
    """
    Convert internal symbol to HIP-3 format.

    Args:
        internal_symbol: Internal symbol like 'XAU', 'TSLA'

    Returns:
        HIP-3 symbol like 'flx:GOLD', 'xyz:TSLA'
        Returns original symbol if not in mapping (for main perps)
    """
    if internal_symbol in HIP3_MAPPING:
        return HIP3_MAPPING[internal_symbol][0]
    return internal_symbol


def to_hip3_with_dex(internal_symbol: str) -> Tuple[str, int]:
    """
    Convert internal symbol to HIP-3 format with DEX index.

    Args:
        internal_symbol: Internal symbol like 'XAU', 'TSLA'

    Returns:
        Tuple of (HIP-3 symbol, DEX index)
        Returns (original, 0) if not in mapping (main perps)
    """
    if internal_symbol in HIP3_MAPPING:
        return HIP3_MAPPING[internal_symbol]
    return (internal_symbol, 0)


def from_hip3_symbol(hip3_symbol: str) -> str:
    """
    Convert HIP-3 format back to internal symbol.

    Args:
        hip3_symbol: HIP-3 symbol like 'flx:GOLD', 'xyz:TSLA'

    Returns:
        Internal symbol like 'XAU', 'TSLA'
    """
    return REVERSE_MAPPING.get(hip3_symbol, hip3_symbol)


def is_hip3_asset(symbol: str) -> bool:
    """Check if symbol requires HIP-3 routing."""
    return symbol in HIP3_MAPPING or ":" in symbol


def get_dex_index(symbol: str) -> int:
    """Get DEX index for a symbol (0 for main perps)."""
    if symbol in HIP3_MAPPING:
        return HIP3_MAPPING[symbol][1]
    return 0


def get_all_hip3_symbols() -> Dict[str, str]:
    """Get all HIP-3 symbol mappings."""
    return {k: v[0] for k, v in HIP3_MAPPING.items()}


# Load additional mappings from config file if exists
def _load_config_mapping():
    """Load HIP-3 mapping from config file if available."""
    config_path = Path(__file__).parent.parent.parent / "config" / "hip3_mapping.json"
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                data = json.load(f)

            # Load symbol mappings from config
            symbol_map = data.get("symbol_mapping", {})
            dex_endpoints = data.get("dex_endpoints", {})

            for category in ["metals", "stocks", "indices", "commodities"]:
                if category in symbol_map:
                    for internal, info in symbol_map[category].items():
                        hip3 = info.get("hip3")
                        dex = info.get("dex")
                        if hip3 and dex:
                            dex_idx = dex_endpoints.get(dex, 0)
                            HIP3_MAPPING[internal] = (hip3, dex_idx)
                            REVERSE_MAPPING[hip3] = internal

            logger.debug(f"Loaded {len(HIP3_MAPPING)} HIP-3 mappings from config")
        except Exception as e:
            logger.warning(f"Failed to load HIP-3 config: {e}")


# Load config on module import
_load_config_mapping()
