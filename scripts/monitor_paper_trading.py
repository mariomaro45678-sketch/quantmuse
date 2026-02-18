#!/usr/bin/env python3
"""
Daily monitoring script for Phase 13 Paper Trading.
Checks health and generates daily reports for both testnet and mock-live tracks.
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def get_daily_stats(mode_filter=None):
    """Get trading statistics for the last 24 hours."""
    conn = sqlite3.connect('hyperliquid.db')
    cursor = conn.cursor()
    
    # Build query
    base_query = """
        SELECT 
            COUNT(*) as total_trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
            SUM(realized_pnl) as total_pnl,
            AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END) as avg_win,
            AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END) as avg_loss
        FROM trades
        WHERE created_at > datetime('now', '-24 hours')
    """
    
    if mode_filter:
        base_query += f" AND strategy_name LIKE '%{mode_filter}%'"
    
    cursor.execute(base_query)
    row = cursor.fetchone()
    
    stats = {
        'total_trades': row[0] or 0,
        'wins': row[1] or 0,
        'losses': row[2] or 0,
        'total_pnl': row[3] or 0.0,
        'avg_win': row[4] or 0.0,
        'avg_loss': row[5] or 0.0,
        'win_rate': (row[1] / row[0] * 100) if row[0] > 0 else 0.0
    }
    
    conn.close()
    return stats

def get_cumulative_stats(start_date=None):
    """Get cumulative statistics since start of Phase 13."""
    conn = sqlite3.connect('hyperliquid.db')
    cursor = conn.cursor()
    
    query = """
        SELECT 
            COUNT(*) as total_trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(realized_pnl) as total_pnl,
            MAX(realized_pnl) as peak_pnl,
            MIN(realized_pnl) as lowest_pnl
        FROM trades
    """
    
    if start_date:
        query += f" WHERE created_at >= '{start_date}'"
    
    cursor.execute(query)
    row = cursor.fetchone()
    
    initial_capital = 10000  # Both tracks started with 5k each
    peak = row[3] or initial_capital
    lowest = row[4] or initial_capital
    max_dd = ((lowest - peak) / peak) * 100 if peak > 0 else 0
    
    stats = {
        'total_trades': row[0] or 0,
        'wins': row[1] or 0,
        'cumulative_pnl': row[2] or 0.0,
        'cumulative_return': (row[2] / initial_capital * 100) if row[2] else 0.0,
        'max_drawdown': max_dd
    }
    
    conn.close()
    return stats

def check_system_health():
    """Check for recent errors and system issues."""
    conn = sqlite3.connect('hyperliquid.db')
    cursor = conn.cursor()
    
    # Check for recent errors
    cursor.execute("""
        SELECT COUNT(*) FROM alerts 
        WHERE type = 'error' 
        AND timestamp > datetime('now', '-24 hours')
    """)
    error_count = cursor.fetchone()[0]
    
    # Check last trade time
    cursor.execute("SELECT MAX(created_at) FROM trades")
    last_trade = cursor.fetchone()[0]
    
    health = {
        'errors_24h': error_count,
        'last_trade': last_trade,
        'status': 'HEALTHY' if error_count == 0 else 'WARNING'
    }
    
    conn.close()
    return health

def print_daily_report():
    """Print formatted daily report."""
    print("=" * 70)
    print(f"PHASE 13 DAILY REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)
    
    # System Health
    health = check_system_health()
    print(f"\n📊 SYSTEM HEALTH: {health['status']}")
    print(f"   Errors (24h): {health['errors_24h']}")
    print(f"   Last Trade: {health['last_trade'] or 'None'}")
    
    # Testnet Crypto Track
    print("\n🔷 TESTNET CRYPTO (BTC, ETH) - Real Execution")
    testnet_daily = get_daily_stats('momentum')
    print(f"   24h Trades: {testnet_daily['total_trades']} "
          f"({testnet_daily['wins']}W / {testnet_daily['losses']}L)")
    print(f"   Win Rate: {testnet_daily['win_rate']:.1f}%")
    print(f"   24h P&L: ${testnet_daily['total_pnl']:.2f}")
    
    # Mock-Live Metals Track
    print("\n🟡 MOCK-LIVE METALS (XAG, XAU) - Simulated Execution")
    mock_daily = get_daily_stats('reversion')
    print(f"   24h Trades: {mock_daily['total_trades']} "
          f"({mock_daily['wins']}W / {mock_daily['losses']}L)")
    print(f"   Win Rate: {mock_daily['win_rate']:.1f}%")
    print(f"   24h P&L: ${mock_daily['total_pnl']:.2f}")
    
    # Cumulative Stats
    print("\n📈 CUMULATIVE (Since Start)")
    cumulative = get_cumulative_stats()
    print(f"   Total Trades: {cumulative['total_trades']}")
    print(f"   Total P&L: ${cumulative['cumulative_pnl']:.2f} "
          f"({cumulative['cumulative_return']:.2f}%)")
    print(f"   Max Drawdown: {cumulative['max_drawdown']:.2f}%")
    
    print("\n" + "=" * 70)

if __name__ == '__main__':
    try:
        print_daily_report()
    except Exception as e:
        print(f"❌ Error generating report: {e}")
        sys.exit(1)
