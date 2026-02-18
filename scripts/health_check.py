#!/usr/bin/env python3
"""
Quick health check script for Phase 13.
Run this to verify both systems are running properly.
"""

import subprocess
import sys
from pathlib import Path

def check_process(name):
    """Check if a process is running."""
    try:
        result = subprocess.run(['pgrep', '-f', name], 
                              capture_output=True, text=True)
        return len(result.stdout.strip()) > 0
    except:
        return False

def check_screen_session(name):
    """Check if a screen session exists."""
    try:
        result = subprocess.run(['screen', '-ls'], 
                              capture_output=True, text=True)
        return name in result.stdout
    except:
        return False

def check_db():
    """Check if database is accessible."""
    db_path = Path('hyperliquid.db')
    return db_path.exists() and db_path.stat().st_size > 0

def main():
    print("🔍 Phase 13 Health Check\n")
    
    checks = {
        'Testnet Crypto Process': check_screen_session('testnet_crypto'),
        'Mock Metals Process': check_screen_session('mock_metals'),
        'Dashboard Process': check_screen_session('dashboard'),
        'Database Accessible': check_db(),
    }
    
    all_passed = True
    for check_name, passed in checks.items():
        status = "✅" if passed else "❌"
        print(f"{status} {check_name}")
        if not passed:
            all_passed = False
    
    print()
    
    if all_passed:
        print("🎉 All systems operational!")
        return 0
    else:
        print("⚠️  Some systems need attention")
        print("\nTo view screens: screen -ls")
        print("To reattach: screen -r <name>")
        return 1

if __name__ == '__main__':
    sys.exit(main())
