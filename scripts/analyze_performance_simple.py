#!/usr/bin/env python3
"""
Simple Trading Performance Analysis - Direct SQLite Access

Analyzes mock trading performance directly from SQLite without heavy dependencies.
"""

import sys
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any

def format_currency(value: float) -> str:
    """Format currency with color coding."""
    if value > 0:
        return f"+${value:,.2f}"
    elif value < 0:
        return f"-${abs(value):,.2f}"
    return "$0.00"

def format_percentage(value: float) -> str:
    """Format percentage."""
    return f"{value:.2f}%"

class SimplePerformanceAnalyzer:
    """Analyzes trading performance directly from SQLite."""

    def __init__(self, db_path: str = "orders_history.db"):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            print(f"Database not found: {db_path}")
            sys.exit(1)

    def get_all_orders(self) -> List[Dict[str, Any]]:
        """Retrieve all orders from database."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        cursor = conn.cursor()

        try:
            # Check if table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
            if not cursor.fetchone():
                print("No 'orders' table found in database")
                return []

            # Get all orders
            cursor.execute("""
                SELECT * FROM orders
                ORDER BY created_at DESC
                LIMIT 10000
            """)

            orders = []
            for row in cursor.fetchall():
                orders.append(dict(row))

            return orders

        finally:
            conn.close()

    def analyze_by_strategy(self, orders: List[Dict]) -> Dict[str, Any]:
        """Aggregate performance by strategy."""
        by_strategy = defaultdict(lambda: {
            'total_trades': 0,
            'total_pnl': 0.0,
            'wins': 0,
            'losses': 0,
            'symbols': set()
        })

        for order in orders:
            if order.get('status') != 'filled':
                continue

            strategy = order.get('strategy_name', 'unknown')
            pnl = order.get('realized_pnl', 0) or 0

            by_strategy[strategy]['total_trades'] += 1
            by_strategy[strategy]['total_pnl'] += pnl
            by_strategy[strategy]['symbols'].add(order.get('symbol', 'UNKNOWN'))

            if pnl > 0:
                by_strategy[strategy]['wins'] += 1
            elif pnl < 0:
                by_strategy[strategy]['losses'] += 1

        # Calculate win rates
        result = {}
        for strategy, stats in by_strategy.items():
            total_decided = stats['wins'] + stats['losses']
            win_rate = (stats['wins'] / total_decided * 100) if total_decided > 0 else 0

            result[strategy] = {
                'total_trades': stats['total_trades'],
                'total_pnl': stats['total_pnl'],
                'wins': stats['wins'],
                'losses': stats['losses'],
                'win_rate': win_rate,
                'avg_pnl_per_trade': stats['total_pnl'] / stats['total_trades'] if stats['total_trades'] > 0 else 0,
                'symbols': sorted(stats['symbols'])
            }

        return result

    def analyze_by_symbol(self, orders: List[Dict]) -> Dict[str, Any]:
        """Aggregate performance by symbol."""
        by_symbol = defaultdict(lambda: {
            'total_trades': 0,
            'total_pnl': 0.0,
            'wins': 0,
            'losses': 0,
            'long_trades': 0,
            'short_trades': 0,
            'strategies': set()
        })

        for order in orders:
            if order.get('status') != 'filled':
                continue

            symbol = order.get('symbol', 'UNKNOWN')
            pnl = order.get('realized_pnl', 0) or 0

            by_symbol[symbol]['total_trades'] += 1
            by_symbol[symbol]['total_pnl'] += pnl
            by_symbol[symbol]['strategies'].add(order.get('strategy_name', 'unknown'))

            side = order.get('side', 'buy').lower()
            if side in ['buy', 'long']:
                by_symbol[symbol]['long_trades'] += 1
            else:
                by_symbol[symbol]['short_trades'] += 1

            if pnl > 0:
                by_symbol[symbol]['wins'] += 1
            elif pnl < 0:
                by_symbol[symbol]['losses'] += 1

        # Calculate metrics
        result = {}
        for symbol, stats in by_symbol.items():
            total_decided = stats['wins'] + stats['losses']
            win_rate = (stats['wins'] / total_decided * 100) if total_decided > 0 else 0

            result[symbol] = {
                'total_trades': stats['total_trades'],
                'total_pnl': stats['total_pnl'],
                'wins': stats['wins'],
                'losses': stats['losses'],
                'win_rate': win_rate,
                'long_trades': stats['long_trades'],
                'short_trades': stats['short_trades'],
                'avg_pnl_per_trade': stats['total_pnl'] / stats['total_trades'] if stats['total_trades'] > 0 else 0,
                'strategies': sorted(stats['strategies'])
            }

        return result

    def print_report(self):
        """Generate and print comprehensive performance report."""
        print("\n" + "="*80)
        print("TRADING PERFORMANCE ANALYSIS")
        print("="*80)

        # Get orders
        orders = self.get_all_orders()

        if not orders:
            print("\nNo orders found in database.")
            return

        # Filter to filled orders
        filled_orders = [o for o in orders if o.get('status') == 'filled']

        print(f"\nTotal Orders: {len(orders)}")
        print(f"Filled Orders: {len(filled_orders)}")

        if not filled_orders:
            print("\nNo filled orders to analyze.")
            return

        # Get time range
        created_times = [o.get('created_at') for o in filled_orders if o.get('created_at')]
        if created_times:
            print(f"Time Range: {min(created_times)} to {max(created_times)}")

        # Overall stats
        total_pnl = sum(o.get('realized_pnl', 0) or 0 for o in filled_orders)
        wins = len([o for o in filled_orders if (o.get('realized_pnl', 0) or 0) > 0])
        losses = len([o for o in filled_orders if (o.get('realized_pnl', 0) or 0) < 0])
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

        print(f"\nOverall PnL: {format_currency(total_pnl)}")
        print(f"Win Rate: {format_percentage(win_rate)} ({wins}W / {losses}L)")

        # By Strategy
        print("\n" + "-"*80)
        print("PERFORMANCE BY STRATEGY")
        print("-"*80)

        by_strategy = self.analyze_by_strategy(filled_orders)
        for strategy, stats in sorted(by_strategy.items(), key=lambda x: x[1]['total_pnl'], reverse=True):
            print(f"\n{strategy.upper()}")
            print(f"  Trades: {stats['total_trades']}")
            print(f"  PnL: {format_currency(stats['total_pnl'])}")
            print(f"  Win Rate: {format_percentage(stats['win_rate'])} ({stats['wins']}W / {stats['losses']}L)")
            print(f"  Avg PnL/Trade: {format_currency(stats['avg_pnl_per_trade'])}")
            print(f"  Symbols: {', '.join(stats['symbols'])}")

        # By Symbol
        print("\n" + "-"*80)
        print("PERFORMANCE BY SYMBOL")
        print("-"*80)

        by_symbol = self.analyze_by_symbol(filled_orders)
        for symbol, stats in sorted(by_symbol.items(), key=lambda x: x[1]['total_pnl'], reverse=True):
            print(f"\n{symbol}")
            print(f"  Trades: {stats['total_trades']} ({stats['long_trades']}L / {stats['short_trades']}S)")
            print(f"  PnL: {format_currency(stats['total_pnl'])}")
            print(f"  Win Rate: {format_percentage(stats['win_rate'])} ({stats['wins']}W / {stats['losses']}L)")
            print(f"  Avg PnL/Trade: {format_currency(stats['avg_pnl_per_trade'])}")
            print(f"  Strategies: {', '.join(stats['strategies'])}")

        # Best/Worst Trades
        print("\n" + "-"*80)
        print("TOP 5 BEST TRADES")
        print("-"*80)

        sorted_by_pnl = sorted(filled_orders, key=lambda o: o.get('realized_pnl', 0) or 0, reverse=True)
        for order in sorted_by_pnl[:5]:
            pnl = order.get('realized_pnl', 0) or 0
            print(f"{order.get('symbol', 'UNK'):6s} | {order.get('strategy_name', 'unknown'):20s} | "
                  f"{order.get('side', 'buy'):4s} {order.get('size', 0):.4f} @ ${order.get('price', 0):.2f} | "
                  f"PnL: {format_currency(pnl)}")

        print("\n" + "-"*80)
        print("TOP 5 WORST TRADES")
        print("-"*80)

        for order in sorted_by_pnl[-5:][::-1]:
            pnl = order.get('realized_pnl', 0) or 0
            print(f"{order.get('symbol', 'UNK'):6s} | {order.get('strategy_name', 'unknown'):20s} | "
                  f"{order.get('side', 'buy'):4s} {order.get('size', 0):.4f} @ ${order.get('price', 0):.2f} | "
                  f"PnL: {format_currency(pnl)}")

        print("\n" + "="*80)


def main():
    analyzer = SimplePerformanceAnalyzer()
    analyzer.print_report()


if __name__ == "__main__":
    main()
