#!/usr/bin/env python3
"""
Trading Performance Analysis Tool

Analyzes mock trading performance from the database and provides:
- Per-strategy breakdown
- Per-symbol performance
- Time-based analysis
- Win/loss patterns
- PnL attribution

Usage:
    python scripts/analyze_trading_performance.py [--format {table|json|csv}]
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.storage.database_manager import DatabaseManager

def format_currency(value: float) -> str:
    """Format currency with color coding."""
    if value > 0:
        return f"${value:,.2f}"
    elif value < 0:
        return f"-${abs(value):,.2f}"
    return "$0.00"

def format_percentage(value: float) -> str:
    """Format percentage."""
    return f"{value:.2f}%"

class PerformanceAnalyzer:
    """Analyzes trading performance from database."""

    def __init__(self):
        self.db = DatabaseManager()

    def get_all_trades(self, hours_back: int = 24) -> List[Dict[str, Any]]:
        """Retrieve all trades from database."""
        # Get orders that have been filled
        orders = self.db.get_order_history(limit=5000)

        # Filter to filled orders within time window
        cutoff = datetime.now() - timedelta(hours=hours_back)
        trades = []

        for order in orders:
            # Convert to trade format
            if order.get('status') == 'filled':
                created_at = datetime.fromisoformat(order.get('created_at', datetime.now().isoformat()))
                if created_at > cutoff:
                    trades.append({
                        'order_id': order.get('order_id'),
                        'symbol': order.get('symbol'),
                        'side': order.get('side'),
                        'size': order.get('size', 0),
                        'price': order.get('fill_price') or order.get('price', 0),
                        'strategy': order.get('strategy_name', 'unknown'),
                        'timestamp': created_at,
                        'pnl': order.get('realized_pnl', 0)
                    })

        return trades

    def analyze_by_strategy(self, trades: List[Dict]) -> Dict[str, Any]:
        """Aggregate performance by strategy."""
        by_strategy = defaultdict(lambda: {
            'total_trades': 0,
            'total_pnl': 0.0,
            'wins': 0,
            'losses': 0,
            'total_volume': 0.0,
            'symbols': set()
        })

        for trade in trades:
            strategy = trade['strategy']
            pnl = trade.get('pnl', 0)

            by_strategy[strategy]['total_trades'] += 1
            by_strategy[strategy]['total_pnl'] += pnl
            by_strategy[strategy]['total_volume'] += trade['size'] * trade['price']
            by_strategy[strategy]['symbols'].add(trade['symbol'])

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
                'total_volume': stats['total_volume'],
                'avg_pnl_per_trade': stats['total_pnl'] / stats['total_trades'] if stats['total_trades'] > 0 else 0,
                'symbols': sorted(stats['symbols'])
            }

        return result

    def analyze_by_symbol(self, trades: List[Dict]) -> Dict[str, Any]:
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

        for trade in trades:
            symbol = trade['symbol']
            pnl = trade.get('pnl', 0)

            by_symbol[symbol]['total_trades'] += 1
            by_symbol[symbol]['total_pnl'] += pnl
            by_symbol[symbol]['strategies'].add(trade['strategy'])

            if trade['side'].lower() in ['buy', 'long']:
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
                'long_pct': stats['long_trades'] / stats['total_trades'] * 100 if stats['total_trades'] > 0 else 0,
                'avg_pnl_per_trade': stats['total_pnl'] / stats['total_trades'] if stats['total_trades'] > 0 else 0,
                'strategies': sorted(stats['strategies'])
            }

        return result

    def analyze_by_hour(self, trades: List[Dict]) -> Dict[int, Any]:
        """Aggregate performance by hour of day."""
        by_hour = defaultdict(lambda: {
            'total_trades': 0,
            'total_pnl': 0.0,
            'wins': 0,
            'losses': 0
        })

        for trade in trades:
            hour = trade['timestamp'].hour
            pnl = trade.get('pnl', 0)

            by_hour[hour]['total_trades'] += 1
            by_hour[hour]['total_pnl'] += pnl

            if pnl > 0:
                by_hour[hour]['wins'] += 1
            elif pnl < 0:
                by_hour[hour]['losses'] += 1

        # Calculate win rates
        result = {}
        for hour in sorted(by_hour.keys()):
            stats = by_hour[hour]
            total_decided = stats['wins'] + stats['losses']
            win_rate = (stats['wins'] / total_decided * 100) if total_decided > 0 else 0

            result[hour] = {
                'total_trades': stats['total_trades'],
                'total_pnl': stats['total_pnl'],
                'win_rate': win_rate,
                'avg_pnl': stats['total_pnl'] / stats['total_trades'] if stats['total_trades'] > 0 else 0
            }

        return result

    def get_best_worst_trades(self, trades: List[Dict], n: int = 10) -> Dict[str, List[Dict]]:
        """Get best and worst trades by PnL."""
        # Sort by PnL
        sorted_trades = sorted(trades, key=lambda t: t.get('pnl', 0), reverse=True)

        return {
            'best': sorted_trades[:n],
            'worst': sorted_trades[-n:][::-1]  # Reverse to show worst first
        }

    def print_report(self, format: str = 'table'):
        """Generate and print comprehensive performance report."""
        print("\n" + "="*80)
        print("TRADING PERFORMANCE ANALYSIS")
        print("="*80)

        # Get trades
        trades = self.get_all_trades(hours_back=168)  # Last week

        if not trades:
            print("\nNo trades found in database.")
            return

        print(f"\nTotal Trades: {len(trades)}")
        print(f"Time Range: {trades[-1]['timestamp']} to {trades[0]['timestamp']}")

        # Overall stats
        total_pnl = sum(t.get('pnl', 0) for t in trades)
        wins = len([t for t in trades if t.get('pnl', 0) > 0])
        losses = len([t for t in trades if t.get('pnl', 0) < 0])
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

        print(f"\nOverall PnL: {format_currency(total_pnl)}")
        print(f"Win Rate: {format_percentage(win_rate)} ({wins}W / {losses}L)")

        # By Strategy
        print("\n" + "-"*80)
        print("PERFORMANCE BY STRATEGY")
        print("-"*80)

        by_strategy = self.analyze_by_strategy(trades)
        if format == 'json':
            print(json.dumps(by_strategy, indent=2, default=str))
        else:
            for strategy, stats in sorted(by_strategy.items(), key=lambda x: x[1]['total_pnl'], reverse=True):
                print(f"\n{strategy.upper()}")
                print(f"  Trades: {stats['total_trades']}")
                print(f"  PnL: {format_currency(stats['total_pnl'])}")
                print(f"  Win Rate: {format_percentage(stats['win_rate'])} ({stats['wins']}W / {stats['losses']}L)")
                print(f"  Avg PnL/Trade: {format_currency(stats['avg_pnl_per_trade'])}")
                print(f"  Volume: {format_currency(stats['total_volume'])}")
                print(f"  Symbols: {', '.join(stats['symbols'])}")

        # By Symbol
        print("\n" + "-"*80)
        print("PERFORMANCE BY SYMBOL")
        print("-"*80)

        by_symbol = self.analyze_by_symbol(trades)
        if format == 'json':
            print(json.dumps(by_symbol, indent=2, default=str))
        else:
            for symbol, stats in sorted(by_symbol.items(), key=lambda x: x[1]['total_pnl'], reverse=True):
                print(f"\n{symbol}")
                print(f"  Trades: {stats['total_trades']} ({stats['long_trades']}L / {stats['short_trades']}S)")
                print(f"  PnL: {format_currency(stats['total_pnl'])}")
                print(f"  Win Rate: {format_percentage(stats['win_rate'])} ({stats['wins']}W / {stats['losses']}L)")
                print(f"  Avg PnL/Trade: {format_currency(stats['avg_pnl_per_trade'])}")
                print(f"  Strategies: {', '.join(stats['strategies'])}")

        # By Hour
        print("\n" + "-"*80)
        print("PERFORMANCE BY HOUR OF DAY (UTC)")
        print("-"*80)

        by_hour = self.analyze_by_hour(trades)
        if format == 'json':
            print(json.dumps(by_hour, indent=2, default=str))
        else:
            for hour in sorted(by_hour.keys()):
                stats = by_hour[hour]
                print(f"{hour:02d}:00 | Trades: {stats['total_trades']:3d} | "
                      f"PnL: {format_currency(stats['total_pnl']):>12s} | "
                      f"Win%: {format_percentage(stats['win_rate']):>6s}")

        # Best/Worst Trades
        print("\n" + "-"*80)
        print("TOP 5 BEST TRADES")
        print("-"*80)

        best_worst = self.get_best_worst_trades(trades, n=5)
        for trade in best_worst['best']:
            print(f"{trade['symbol']:6s} | {trade['strategy']:20s} | "
                  f"{trade['side']:4s} {trade['size']:.4f} @ ${trade['price']:.2f} | "
                  f"PnL: {format_currency(trade['pnl'])}")

        print("\n" + "-"*80)
        print("TOP 5 WORST TRADES")
        print("-"*80)

        for trade in best_worst['worst']:
            print(f"{trade['symbol']:6s} | {trade['strategy']:20s} | "
                  f"{trade['side']:4s} {trade['size']:.4f} @ ${trade['price']:.2f} | "
                  f"PnL: {format_currency(trade['pnl'])}")

        print("\n" + "="*80)


def main():
    parser = argparse.ArgumentParser(description="Analyze trading performance")
    parser.add_argument('--format', choices=['table', 'json', 'csv'], default='table',
                       help='Output format (default: table)')
    args = parser.parse_args()

    analyzer = PerformanceAnalyzer()
    analyzer.print_report(format=args.format)


if __name__ == "__main__":
    main()
