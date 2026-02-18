#!/usr/bin/env python3
"""
Close all open positions on all DEXes (emergency script).
Usage: python scripts/close_all_positions.py
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.chdir(Path(__file__).parent.parent)

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
import eth_account
import requests

WALLET = "0x84d7b59c75123aed485c50fb6684a4a47dedc4ff"
DEXES = ['xyz', 'flx']


def main():
    secret = os.environ.get('HYPERLIQUID_API_SECRET')
    if not secret:
        print("ERROR: HYPERLIQUID_API_SECRET environment variable not set")
        sys.exit(1)

    account = eth_account.Account.from_key(secret)
    exchange = Exchange(account, constants.MAINNET_API_URL)

    print("=" * 50)
    print("CLOSING ALL POSITIONS")
    print("=" * 50)

    closed = 0
    for dex in DEXES:
        print(f"\nChecking {dex} DEX...")
        try:
            payload = {'type': 'clearinghouseState', 'user': WALLET, 'dex': dex}
            resp = requests.post('https://api.hyperliquid.xyz/info', json=payload)
            data = resp.json()

            for pos in data.get('assetPositions', []):
                p = pos['position']
                size = float(p['szi'])
                if size != 0:
                    symbol = f"{dex}:{p['coin']}"
                    side = 'LONG' if size > 0 else 'SHORT'
                    print(f"  Closing {symbol} {side} {abs(size):.4f}...")

                    try:
                        result = exchange.market_close(symbol)
                        if result.get('status') == 'ok':
                            print(f"    SUCCESS")
                            closed += 1
                        else:
                            print(f"    Result: {result}")
                    except Exception as e:
                        print(f"    ERROR: {e}")
        except Exception as e:
            print(f"  Error checking {dex}: {e}")

    print(f"\n{'=' * 50}")
    print(f"Closed {closed} positions")
    print("=" * 50)


if __name__ == "__main__":
    main()
