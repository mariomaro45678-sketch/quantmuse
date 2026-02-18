#!/usr/bin/env python3
"""
Trading Performance Analysis from JSONL Trade Log

Reads from logs/trades.jsonl and provides comprehensive analysis.
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Any

def format_currency(value: float) -> str:
    """Format currency with sign."""
    if value > 0:
        return f"+${value:,.2f}"
    elif value < 0:
        return f"-${abs(value):,.2f}"
    return "$0.00"

def format_percentage(value: float) -> str:
    """Format percentage."""
    return f"{value:.2f}%"

class TradeAnalyzer:
    """Analyzes trades from JSONL log."""

    def __init__(self, log_path: str = "logs/trades.jsonl"):
        self.log_path = Path(log_path)

    def load_trades(self) -> List[Dict[str, Any]]:
        """Load all trades from JSONL file."""
        if not self.log_path.exists():
            print(f"Trade log not found: {self.log_path}")
            print("\nTo generate trade data, run multi-strategy trading:")
            print("  python3 scripts/run_multi_strategy.py --duration 1")
            return []

        trades = []
        with open(self.log_path, "r") as f:
            for line in f:
                if line.strip():
                    trades.append(json.loads(line))

        return trades

    def analyze_by_strategy(self, trades: List[Dict]) -> Dict[str, Any]:
        """Aggregate performance by strategy."""
        by_strategy = defaultdict(lambda: {
            'total_trades': 0,
            'total_pnl': 0.0,
            'total_fees': 0.0,
            'total_slippage': 0.0,
            'wins': 0,
            'losses': 0,
            'symbols': set()
        })

        for trade in trades:
            strategy = trade.get('strategy', 'unknown')
            pnl = trade.get('pnl', 0)

            by_strategy[strategy]['total_trades'] += 1
            by_strategy[strategy]['total_pnl'] += pnl
            by_strategy[strategy]['total_fees'] += trade.get('fee', 0)
            by_strategy[strategy]['total_slippage'] += abs(trade.get('slippage', 0))
            by_strategy[strategy]['symbols'].add(trade['symbol'])

            if pnl > 0:
                by_strategy[strategy]['wins'] += 1
            elif pnl < 0:
                by_strategy[strategy]['losses'] += 1

        # Calculate metrics
        result = {}
        for strategy, stats in by_strategy.items():
            total_decided = stats['wins'] + stats['losses']
            win_rate = (stats['wins'] / total_decided * 100) if total_decided > 0 else 0

            result[strategy] = {
                'total_trades': stats['total_trades'],
                'total_pnl': stats['total_pnl'],
                'total_fees': stats['total_fees'],
                'avg_slippage_bps': (stats['total_slippage'] / stats['total_trades'] * 10000) if stats['total_trades'] > 0 else 0,
                'wins': stats['wins'],
                'losses': stats['losses'],
                'win_rate': win_rate,
                'avg_pnl_per_trade': stats['total_pnl'] / stats['total_trades'] if stats['total_trades'] > 0 else 0,
                'symbols': sorted(stats['symbols'])
            }

        return result

    def analyze_by_symbol(self, trades: List[Dict]) -> Dict[str, Any]:
        """Aggregate performance by symbol."""
        by_symbol = defaultdict(lambda: {
            'total_trades': 0,
            'total_pnl': 0.0,
            'total_fees': 0.0,
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
            by_symbol[symbol]['total_fees'] += trade.get('fee', 0)
            by_symbol[symbol]['strategies'].add(trade.get('strategy', 'unknown'))

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
                'total_fees': stats['total_fees'],
                'net_pnl': stats['total_pnl'] - stats['total_fees'],
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
        print("TRADING PERFORMANCE ANALYSIS (from JSONL log)")
        print("="*80)

        # Load trades
        trades = self.load_trades()

        if not trades:
            print("\nNo trades found.")
            return

        print(f"\nTotal Trades: {len(trades)}")

        # Get time range
        timestamps = [t['timestamp'] for t in trades]
        start_time = datetime.fromtimestamp(min(timestamps))
        end_time = datetime.fromtimestamp(max(timestamps))
        print(f"Time Range: {start_time} to {end_time}")
        print(f"Duration: {end_time - start_time}")

        # Overall stats
        total_pnl = sum(t.get('pnl', 0) for t in trades)
        total_fees = sum(t.get('fee', 0) for t in trades)
        net_pnl = total_pnl - total_fees
        wins = len([t for t in trades if t.get('pnl', 0) > 0])
        losses = len([t for t in trades if t.get('pnl', 0) < 0])
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

        avg_slippage = sum(abs(t.get('slippage', 0)) for t in trades) / len(trades) if trades else 0

        print(f"\n{'='*80}")
        print("OVERALL PERFORMANCE")
        print(f"{'='*80}")
        print(f"Gross PnL:      {format_currency(total_pnl)}")
        print(f"Total Fees:     {format_currency(total_fees)}")
        print(f"Net PnL:        {format_currency(net_pnl)}")
        print(f"Win Rate:       {format_percentage(win_rate)} ({wins}W / {losses}L / {len(trades)-(wins+losses)}BE)")
        print(f"Avg Slippage:   {avg_slippage*10000:.2f} bps")
        print(f"Avg PnL/Trade:  {format_currency(total_pnl / len(trades))}")

        # By Strategy
        print(f"\n{'='*80}")
        print("PERFORMANCE BY STRATEGY")
        print(f"{'='*80}")

        by_strategy = self.analyze_by_strategy(trades)
        for strategy, stats in sorted(by_strategy.items(), key=lambda x: x[1]['total_pnl'], reverse=True):
            print(f"\n{strategy.upper()}")
            print(f"  Trades:         {stats['total_trades']}")
            print(f"  Gross PnL:      {format_currency(stats['total_pnl'])}")
            print(f"  Fees:           {format_currency(stats['total_fees'])}")
            print(f"  Net PnL:        {format_currency(stats['total_pnl'] - stats['total_fees'])}")
            print(f"  Win Rate:       {format_percentage(stats['win_rate'])} ({stats['wins']}W / {stats['losses']}L)")
            print(f"  Avg PnL/Trade:  {format_currency(stats['avg_pnl_per_trade'])}")
            print(f"  Avg Slippage:   {stats['avg_slippage_bps']:.2f} bps")
            print(f"  Symbols:        {', '.join(stats['symbols'])}")

        # By Symbol
        print(f"\n{'='*80}")
        print("PERFORMANCE BY SYMBOL")
        print(f"{'='*80}")

        by_symbol = self.analyze_by_symbol(trades)

        # Sort by net PnL
        sorted_symbols = sorted(by_symbol.items(), key=lambda x: x[1]['net_pnl'], reverse=True)

        for symbol, stats in sorted_symbols:
            print(f"\n{symbol}")
            print(f"  Trades:         {stats['total_trades']} ({stats['long_trades']}L / {stats['short_trades']}S)")
            print(f"  Gross PnL:      {format_currency(stats['total_pnl'])}")
            print(f"  Fees:           {format_currency(stats['total_fees'])}")
            print(f"  Net PnL:        {format_currency(stats['net_pnl'])}")
            print(f"  Win Rate:       {format_percentage(stats['win_rate'])} ({stats['wins']}W / {stats['losses']}L)")
            print(f"  Avg PnL/Trade:  {format_currency(stats['avg_pnl_per_trade'])}")
            print(f"  Strategies:     {', '.join(stats['strategies'])}")

        # Best/Worst Trades
        print(f"\n{'='*80}")
        print("TOP 10 BEST TRADES")
        print(f"{'='*80}")

        sorted_trades = sorted(trades, key=lambda t: t.get('pnl', 0), reverse=True)

        for trade in sorted_trades[:10]:
            pnl = trade.get('pnl', 0)
            ts = datetime.fromtimestamp(trade['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
            print(f"{ts} | {trade['symbol']:6s} | {trade.get('strategy', 'unknown'):20s} | "
                  f"{trade['side']:4s} {trade['size']:.4f} @ ${trade['fill_price']:.2f} | "
                  f"PnL: {format_currency(pnl)} | Fee: ${trade.get('fee', 0):.2f}")

        print(f"\n{'='*80}")
        print("TOP 10 WORST TRADES")
        print(f"{'='*80}")

        for trade in sorted_trades[-10:][::-1]:
            pnl = trade.get('pnl', 0)
            ts = datetime.fromtimestamp(trade['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
            print(f"{ts} | {trade['symbol']:6s} | {trade.get('strategy', 'unknown'):20s} | "
                  f"{trade['side']:4s} {trade['size']:.4f} @ ${trade['fill_price']:.2f} | "
                  f"PnL: {format_currency(pnl)} | Fee: ${trade.get('fee', 0):.2f}")

        print("\n" + "="*80)


def main():
    analyzer = TradeAnalyzer()
    analyzer.print_report()


if __name__ == "__main__":
    main()
