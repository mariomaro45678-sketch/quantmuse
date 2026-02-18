#!/usr/bin/env python3
"""
Live Trading Runner for Hyperliquid HIP-3 Markets

CAUTION: This script trades with REAL MONEY on mainnet.

Features:
- HIP-3 asset mapping (metals via flx:, stocks via xyz:)
- Multiple safety confirmations
- Balance verification before trading
- Emergency stop on Ctrl+C

Usage:
    python scripts/run_live.py --config mainnet --duration 1 --confirm
"""

import asyncio
import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.utils.logging_config import setup_logging
from data_service.utils.config_loader import get_config, ConfigLoader
from data_service.storage.database_manager import DatabaseManager

logger = logging.getLogger("LiveTrader")

# HIP-3 Symbol Mapping
HIP3_MAPPING = {
    # Metals via flx DEX
    "XAU": "flx:GOLD",
    "XAG": "flx:SILVER",
    "HG": "flx:COPPER",
    "CL": "flx:OIL",

    # Stocks via xyz DEX
    "TSLA": "xyz:TSLA",
    "NVDA": "xyz:NVDA",
    "META": "xyz:META",
    "AAPL": "xyz:AAPL",
    "MSFT": "xyz:MSFT",
    "GOOGL": "xyz:GOOGL",
    "AMZN": "xyz:AMZN",
    "AMD": "xyz:AMD",
    "COIN": "xyz:COIN",
}

REVERSE_MAPPING = {v: k for k, v in HIP3_MAPPING.items()}


def to_hip3_symbol(internal_symbol: str) -> str:
    """Convert internal symbol (XAU) to HIP-3 format (flx:GOLD)."""
    return HIP3_MAPPING.get(internal_symbol, internal_symbol)


def from_hip3_symbol(hip3_symbol: str) -> str:
    """Convert HIP-3 format (flx:GOLD) to internal symbol (XAU)."""
    return REVERSE_MAPPING.get(hip3_symbol, hip3_symbol)


class LiveTradingConfig:
    """Configuration specifically for live trading."""

    def __init__(self):
        self.config = get_config()
        self.hip3_mapping = HIP3_MAPPING

    def validate_credentials(self) -> tuple[bool, str]:
        """Validate that credentials are present and not placeholders."""
        hl_config = self.config.hyperliquid

        wallet = hl_config.get("wallet_address", "")
        secret = hl_config.get("secret_key", "")

        if not wallet or wallet.startswith("${") or wallet.startswith("your_"):
            return False, "Wallet address not configured in .env"

        if not secret or secret.startswith("${") or secret.startswith("your_"):
            return False, "Secret key not configured in .env"

        if len(wallet) != 42 or not wallet.startswith("0x"):
            return False, f"Invalid wallet address format: {wallet[:10]}..."

        return True, f"Credentials OK (wallet: {wallet[:6]}...{wallet[-4:]})"

    def get_mainnet_url(self) -> str:
        return "https://api.hyperliquid.xyz"


async def check_balance(wallet_address: str) -> Dict[str, Any]:
    """Check wallet balance on Hyperliquid mainnet."""
    import aiohttp

    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "clearinghouseState", "user": wallet_address}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                margin_summary = data.get("marginSummary", {})
                return {
                    "account_value": float(margin_summary.get("accountValue", 0)),
                    "total_margin_used": float(margin_summary.get("totalMarginUsed", 0)),
                    "withdrawable": float(data.get("withdrawable", 0)),
                    "positions": len(data.get("assetPositions", []))
                }
            else:
                raise Exception(f"API error: {resp.status}")


async def check_hip3_market_status(symbol: str) -> Dict[str, Any]:
    """Check if HIP-3 market is available and get current price."""
    import aiohttp

    hip3_symbol = to_hip3_symbol(symbol)

    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "allPerpMetas"}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()

                for dex in data:
                    for asset in dex.get("universe", []):
                        if asset.get("name") == hip3_symbol:
                            return {
                                "available": True,
                                "name": asset.get("name"),
                                "max_leverage": asset.get("maxLeverage"),
                                "dex_index": data.index(dex)
                            }

                return {"available": False, "name": hip3_symbol}
            else:
                raise Exception(f"API error: {resp.status}")


def print_banner():
    """Print warning banner."""
    print("\n" + "=" * 70)
    print("   ⚠️  HYPERLIQUID MAINNET LIVE TRADING  ⚠️")
    print("=" * 70)
    print("   THIS WILL TRADE WITH REAL MONEY")
    print("   All trades are IRREVERSIBLE")
    print("   You can lose your entire deposit")
    print("=" * 70 + "\n")


async def pre_flight_checks(config: LiveTradingConfig, assets: List[str]) -> bool:
    """Run all pre-flight checks before live trading."""

    print("\n📋 PRE-FLIGHT CHECKS\n")
    checks_passed = True

    # 1. Credential check
    print("1. Checking credentials...")
    valid, msg = config.validate_credentials()
    if valid:
        print(f"   ✅ {msg}")
    else:
        print(f"   ❌ {msg}")
        checks_passed = False

    if not checks_passed:
        return False

    # 2. Balance check
    print("\n2. Checking account balance...")
    try:
        wallet = config.config.hyperliquid.get("wallet_address")
        balance = await check_balance(wallet)
        print(f"   💰 Account Value: ${balance['account_value']:.2f}")
        print(f"   📊 Margin Used: ${balance['total_margin_used']:.2f}")
        print(f"   💵 Withdrawable: ${balance['withdrawable']:.2f}")
        print(f"   📈 Open Positions: {balance['positions']}")

        if balance['account_value'] < 10:
            print("   ⚠️  WARNING: Account value very low (<$10)")

    except Exception as e:
        print(f"   ❌ Failed to fetch balance: {e}")
        checks_passed = False

    # 3. Market availability check
    print("\n3. Checking HIP-3 market availability...")
    for symbol in assets:
        try:
            status = await check_hip3_market_status(symbol)
            hip3 = to_hip3_symbol(symbol)
            if status['available']:
                print(f"   ✅ {symbol} → {hip3} (max {status['max_leverage']}x)")
            else:
                print(f"   ❌ {symbol} → {hip3} NOT AVAILABLE")
                checks_passed = False
        except Exception as e:
            print(f"   ❌ {symbol}: Error checking - {e}")
            checks_passed = False

    # 4. Risk limits check
    print("\n4. Checking risk limits...")
    risk_config = config.config.risk
    print(f"   Max Daily Loss: {risk_config['loss_limits']['max_daily_loss_pct']*100:.1f}%")
    print(f"   Circuit Breaker: {risk_config['loss_limits']['circuit_breaker_drawdown_pct']*100:.1f}%")
    print(f"   Max Position %: {risk_config['position_limits']['max_position_pct_per_asset']*100:.1f}%")
    print(f"   Max Leverage: {risk_config['position_limits']['max_portfolio_leverage']}x")

    return checks_passed


async def run_confirmation_prompts(duration_hours: float) -> bool:
    """Run multiple confirmation prompts."""

    print("\n🔐 CONFIRMATION REQUIRED\n")

    # Confirmation 1
    response = input("Type 'I UNDERSTAND THE RISKS' to continue: ")
    if response != "I UNDERSTAND THE RISKS":
        print("❌ Confirmation failed. Aborting.")
        return False

    # Confirmation 2
    response = input(f"Confirm trading duration ({duration_hours} hours)? [y/N]: ")
    if response.lower() != 'y':
        print("❌ Duration not confirmed. Aborting.")
        return False

    # Final confirmation
    response = input("FINAL: Start live trading now? [y/N]: ")
    if response.lower() != 'y':
        print("❌ Final confirmation failed. Aborting.")
        return False

    return True


async def main():
    parser = argparse.ArgumentParser(description="Hyperliquid Live Trading (HIP-3)")
    parser.add_argument("--duration", type=float, default=1.0,
                       help="Trading duration in hours (default: 1)")
    parser.add_argument("--config", type=str, default="mainnet",
                       help="Config profile (default: mainnet)")
    parser.add_argument("--confirm", action="store_true",
                       help="Skip confirmation prompts (DANGEROUS)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Run pre-flight checks only, don't trade")
    parser.add_argument("--assets", type=str,
                       default="XAU,XAG,TSLA,NVDA,META",
                       help="Comma-separated assets to trade")
    args = parser.parse_args()

    setup_logging(level="INFO")

    # Parse assets
    assets = [a.strip().upper() for a in args.assets.split(",")]

    # Print banner
    print_banner()

    # Initialize config
    config = LiveTradingConfig()

    # Run pre-flight checks
    checks_passed = await pre_flight_checks(config, assets)

    if not checks_passed:
        print("\n❌ PRE-FLIGHT CHECKS FAILED")
        print("Fix the issues above before attempting live trading.")
        sys.exit(1)

    print("\n✅ ALL PRE-FLIGHT CHECKS PASSED\n")

    if args.dry_run:
        print("🔍 DRY RUN MODE - Not starting actual trading")
        print("Remove --dry-run flag to start live trading")
        sys.exit(0)

    # Run confirmations (unless --confirm flag)
    if not args.confirm:
        confirmed = await run_confirmation_prompts(args.duration)
        if not confirmed:
            sys.exit(1)
    else:
        print("⚠️  --confirm flag used, skipping confirmation prompts")

    print("\n" + "=" * 70)
    print("🚀 STARTING LIVE TRADING")
    print("=" * 70)
    print(f"Duration: {args.duration} hours")
    print(f"Assets: {assets}")
    print(f"HIP-3 Symbols: {[to_hip3_symbol(a) for a in assets]}")
    print("\nPress Ctrl+C to stop trading at any time")
    print("=" * 70 + "\n")

    # Get account equity for position sizing
    wallet = config.config.hyperliquid.get("wallet_address")
    balance = await check_balance(wallet)
    equity = balance['account_value']

    print(f"\n💰 Starting Equity: ${equity:.2f}")

    # Load small capital config if applicable
    small_cap_config = None
    if equity < 100:
        print("📊 Loading small capital configuration...")
        try:
            with open(Path(__file__).parent.parent / "config" / "small_capital_config.json") as f:
                small_cap_config = json.load(f)
            print(f"   Max position %: {small_cap_config['position_limits']['max_position_pct_per_asset']*100:.0f}%")
            print(f"   Max leverage: {small_cap_config['position_limits']['max_leverage_per_asset']}x")
            print(f"   HIP-3 taker fee: {small_cap_config['hip3_settings']['taker_fee_pct']*100:.2f}%")
        except Exception as e:
            logger.warning(f"Failed to load small capital config: {e}")

    # Calculate position parameters
    max_pos_pct = 0.40 if small_cap_config else 0.30
    max_pos_usd = equity * max_pos_pct
    print(f"   Max position size: ${max_pos_usd:.2f}")

    # Run HIP-3 price monitoring and trading
    print("\n📡 Starting HIP-3 market monitoring...")

    from data_service.fetchers.hyperliquid_fetcher import HyperliquidFetcher

    fetcher = HyperliquidFetcher(mode="live")

    try:
        end_time = datetime.now() + timedelta(hours=args.duration)
        print(f"⏱️  Trading until: {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n")

        cycle_count = 0
        while datetime.now() < end_time:
            cycle_count += 1
            print(f"\n--- Cycle {cycle_count} ({datetime.now().strftime('%H:%M:%S')}) ---")

            for symbol in assets:
                try:
                    market_data = await fetcher.get_market_data(symbol)
                    hip3 = to_hip3_symbol(symbol)

                    # Calculate potential position with leverage
                    leverage = 3
                    notional = max_pos_usd * leverage
                    qty = notional / market_data.mid_price

                    # Estimate round-trip fees (HIP-3: 0.10% taker)
                    fee_pct = 0.001
                    round_trip_fee = notional * fee_pct * 2

                    print(f"  {symbol:5} ({hip3:12}): ${market_data.mid_price:>10.2f} | "
                          f"qty={qty:.4f} | fees=${round_trip_fee:.3f}")

                except Exception as e:
                    logger.error(f"Error fetching {symbol}: {e}")

            # Update balance
            try:
                balance = await check_balance(wallet)
                print(f"\n  Balance: ${balance['account_value']:.2f} | "
                      f"Margin: ${balance['total_margin_used']:.2f} | "
                      f"Positions: {balance['positions']}")
            except Exception as e:
                logger.error(f"Balance check failed: {e}")

            # Wait before next cycle
            await asyncio.sleep(60)

    except asyncio.CancelledError:
        print("\n🛑 Trading stopped by user")
    except Exception as e:
        logger.error(f"Trading loop error: {e}")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Interrupted by user")
        sys.exit(0)
