import asyncio
import logging
import sys
import os
import subprocess
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from data_service.utils.logging_config import setup_logging
from data_service.storage.database_manager import DatabaseManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Phase10_Verification")

async def run_final_gate():
    print("\n" + "🏁" * 20)
    print(" PHASE 10 FINAL VERIFICATION GATE")
    print("🏁" * 20 + "\n")
    
    results = []

    def log_check(name, success):
        icon = "✅" if success else "❌"
        print(f"{icon} {name}")
        results.append(success)

    # 1. Production Print Audit
    print("\n[CHECK 1] Auditing bare print() statements in core code...")
    # Excluding migrations, verify scripts, and tests
    cmd_grep = 'grep -r "print(" data_service/ backend/ main.py | grep -v "test_" | grep -v "__pycache__" | wc -l'
    res_grep = subprocess.run(cmd_grep, shell=True, capture_output=True, text=True)
    count = int(res_grep.stdout.strip())
    log_check(f"Zero bare prints in production logic (Found: {count})", count == 0)

    # 2. Secret Redaction
    print("\n[CHECK 2] Verifying secret redaction in logs...")
    cmd_redact = "python verify_phase_10_hardening.py"
    res_redact = subprocess.run(cmd_redact, shell=True, capture_output=True, text=True)
    log_check("Secret redaction filter functional", "✅ Secret redaction works!" in res_redact.stdout)

    # 3. Unified Database Check
    print("\n[CHECK 3] Verifying unified database tables...")
    db = DatabaseManager()
    tables = []
    with db.db_path.open() as f: # Just ensure file exists
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cursor.fetchall()]
        conn.close()
    
    required = ['candles', 'news', 'trades', 'risk_snapshots', 'sentiment_factors', 'alerts']
    missing = [t for t in required if t not in tables]
    log_check(f"Unified database has all required tables (Missing: {missing})", len(missing) == 0)

    # 4. Docker Config Sanity
    print("\n[CHECK 4] Verifying Docker configuration files...")
    log_check("Dockerfile exists", Path("Dockerfile").exists())
    log_check("docker-compose.yml exists", Path("docker-compose.yml").exists())
    
    # 5. Monitoring API
    print("\n[CHECK 5] Testing Monitoring API logic...")
    from backend.dashboard_app import get_logs
    try:
        # Mock request-like behavior for testing the function logic
        # We just check if it's callable and returns a list
        res_logs = await get_logs(limit=5)
        log_check("Logs API endpoint function logic valid", isinstance(res_logs, list))
    except Exception as e:
        log_check(f"Logs API logic failed: {e}", False)

    # 6. README & Runbook
    print("\n[CHECK 6] Verifying documentation...")
    log_check("README.md contains Quick Start instructions", "Quick Start" in Path("README.md").read_text())
    log_check("Runbook.md exists in docs/", Path("docs/runbook.md").exists())

    # FINAL STATUS
    print("\n" + "="*40)
    if all(results):
        print(" 🎉 PHASE 10 COMPLETE: SYSTEM IS PRODUCTION-READY!")
    else:
        print(" ❌ PHASE 10 VERIFICATION FAILED: Fix blockers above.")
    print("="*40 + "\n")

if __name__ == "__main__":
    asyncio.run(run_final_gate())
