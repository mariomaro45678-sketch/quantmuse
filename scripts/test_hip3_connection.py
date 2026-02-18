#!/usr/bin/env python3
"""
Quick HIP-3 Connection Test

Tests:
1. API connectivity to Hyperliquid mainnet
2. Wallet balance check
3. HIP-3 asset availability (flx:GOLD, xyz:TSLA, etc.)
4. Price fetching for HIP-3 markets

Usage:
    python scripts/test_hip3_connection.py
"""

import asyncio
import aiohttp
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
import os

load_dotenv()

API_URL = "https://api.hyperliquid.xyz/info"

# Test assets
TEST_ASSETS = {
    "flx:GOLD": "Gold (XAU)",
    "flx:SILVER": "Silver (XAG)",
    "flx:COPPER": "Copper (HG)",
    "xyz:TSLA": "Tesla",
    "xyz:NVDA": "NVIDIA",
    "xyz:META": "Meta",
}


async def test_api_connection():
    """Test basic API connectivity."""
    print("\n1. Testing API connectivity...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json={"type": "meta"}) as resp:
                if resp.status == 200:
                    print("   [OK] API responding")
                    return True
                else:
                    print(f"   [FAIL] API returned {resp.status}")
                    return False
    except Exception as e:
        print(f"   [FAIL] Connection error: {e}")
        return False


async def test_wallet_balance():
    """Test wallet balance fetch."""
    print("\n2. Testing wallet balance...")

    wallet = os.getenv("HYPERLIQUID_WALLET_ADDRESS")
    if not wallet:
        print("   [SKIP] No wallet address in .env")
        return None

    print(f"   Wallet: {wallet[:6]}...{wallet[-4:]}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json={
                "type": "clearinghouseState",
                "user": wallet
            }) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    margin = data.get("marginSummary", {})
                    equity = float(margin.get("accountValue", 0))
                    print(f"   [OK] Account Value: ${equity:.2f}")
                    return equity
                else:
                    print(f"   [FAIL] API returned {resp.status}")
                    return None
    except Exception as e:
        print(f"   [FAIL] Error: {e}")
        return None


async def test_hip3_markets():
    """Test HIP-3 market availability."""
    print("\n3. Testing HIP-3 market availability...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json={"type": "allPerpMetas"}) as resp:
                if resp.status != 200:
                    print(f"   [FAIL] API returned {resp.status}")
                    return False

                data = await resp.json()

                # Build flat list of all assets
                all_assets = {}
                for dex_idx, dex in enumerate(data):
                    for asset in dex.get("universe", []):
                        name = asset.get("name")
                        all_assets[name] = {
                            "dex_idx": dex_idx,
                            "max_leverage": asset.get("maxLeverage"),
                        }

                # Check test assets
                found = 0
                for hip3_name, display_name in TEST_ASSETS.items():
                    if hip3_name in all_assets:
                        info = all_assets[hip3_name]
                        print(f"   [OK] {hip3_name:12} ({display_name}) - DEX {info['dex_idx']}, max {info['max_leverage']}x")
                        found += 1
                    else:
                        print(f"   [MISSING] {hip3_name:12} ({display_name})")

                print(f"\n   Found {found}/{len(TEST_ASSETS)} test assets")
                return found == len(TEST_ASSETS)

    except Exception as e:
        print(f"   [FAIL] Error: {e}")
        return False


async def test_price_fetch():
    """Test fetching prices for HIP-3 assets."""
    print("\n4. Testing HIP-3 price fetch...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json={"type": "allMids"}) as resp:
                if resp.status != 200:
                    print(f"   [FAIL] API returned {resp.status}")
                    return False

                data = await resp.json()

                for hip3_name, display_name in TEST_ASSETS.items():
                    if hip3_name in data:
                        price = float(data[hip3_name])
                        print(f"   [OK] {hip3_name:12}: ${price:>10.2f}")
                    else:
                        print(f"   [MISSING] {hip3_name:12} - no price data")

                return True

    except Exception as e:
        print(f"   [FAIL] Error: {e}")
        return False


async def main():
    print("=" * 60)
    print("   HIP-3 CONNECTION TEST")
    print("=" * 60)

    results = []

    # Run all tests
    results.append(("API Connection", await test_api_connection()))
    results.append(("Wallet Balance", await test_wallet_balance() is not None))
    results.append(("HIP-3 Markets", await test_hip3_markets()))
    results.append(("Price Fetch", await test_price_fetch()))

    # Summary
    print("\n" + "=" * 60)
    print("   SUMMARY")
    print("=" * 60)

    passed = 0
    for name, result in results:
        status = "[OK]" if result else "[FAIL]"
        print(f"   {status:6} {name}")
        if result:
            passed += 1

    print(f"\n   {passed}/{len(results)} tests passed")

    if passed == len(results):
        print("\n   Ready for live trading!")
        return 0
    else:
        print("\n   Fix issues before proceeding")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
