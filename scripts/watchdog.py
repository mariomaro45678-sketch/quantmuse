#!/usr/bin/env python3
"""
Watchdog Service for News Collector

Features:
    1. Monitors news collector process health
    2. Checks health file for staleness
    3. Auto-restarts on crash or hang
    4. Logs all events with timestamps
    5. Configurable check interval and thresholds
    6. Can run as daemon or one-shot check

Usage:
    # Run as daemon (recommended)
    python scripts/watchdog.py --daemon

    # One-shot check (for cron)
    python scripts/watchdog.py --check

    # With custom settings
    python scripts/watchdog.py --daemon --check-interval 60 --max-stale 600
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

# Setup paths
PROJECT_ROOT = Path(__file__).parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
HEALTH_FILE = LOGS_DIR / "news_collector_health.json"
WATCHDOG_LOG = LOGS_DIR / "watchdog.log"
COLLECTOR_LOG_PATTERN = "news_collector_{timestamp}.log"
PID_FILE = LOGS_DIR / "news_collector.pid"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(WATCHDOG_LOG),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class WatchdogConfig:
    """Configuration for the watchdog service."""
    check_interval: int = 60  # seconds between checks
    max_stale_seconds: int = 600  # health file older than this = unhealthy
    max_restart_attempts: int = 5  # max restarts before giving up
    restart_cooldown: int = 300  # seconds between restart attempts
    collector_symbols: str = "XAU,XAG,TSLA,NVDA,AMD,COIN,AAPL,GOOGL,MSFT,AMZN,META"
    collector_interval: int = 5  # minutes


class CollectorStatus:
    """Status information about the news collector."""
    def __init__(self):
        self.is_running: bool = False
        self.pid: Optional[int] = None
        self.health_status: str = "unknown"
        self.last_cycle: Optional[datetime] = None
        self.uptime_seconds: float = 0
        self.cycles: int = 0
        self.articles_processed: int = 0
        self.consecutive_failures: int = 0
        self.health_file_age: Optional[float] = None
        self.error: Optional[str] = None


def find_collector_pid() -> Optional[int]:
    """Find the PID of the running news collector process."""
    try:
        # Check PID file first
        if PID_FILE.exists():
            pid = int(PID_FILE.read_text().strip())
            if _is_process_running(pid):
                return pid

        # Fall back to pgrep
        result = subprocess.run(
            ["pgrep", "-f", "news_collector.py"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            for pid_str in pids:
                if pid_str:
                    pid = int(pid_str)
                    # Verify it's our collector
                    if _is_news_collector(pid):
                        return pid
        return None
    except Exception as e:
        logger.debug("Error finding collector PID: %s", e)
        return None


def _is_process_running(pid: int) -> bool:
    """Check if a process with given PID is running (not zombie)."""
    try:
        os.kill(pid, 0)
        # Check if it's a zombie - zombies respond to kill(0) but aren't really running
        if _is_zombie(pid):
            return False
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_zombie(pid: int) -> bool:
    """Check if a process is a zombie (defunct)."""
    try:
        status_path = Path(f"/proc/{pid}/status")
        if status_path.exists():
            content = status_path.read_text()
            for line in content.splitlines():
                if line.startswith("State:"):
                    # State: Z (zombie) or State: Z (defunct)
                    return "Z" in line.split()[1]
        return False
    except Exception:
        return False


def _is_news_collector(pid: int) -> bool:
    """Verify that a PID belongs to the news collector."""
    try:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if cmdline_path.exists():
            cmdline = cmdline_path.read_text()
            return "news_collector" in cmdline
        return False
    except Exception:
        return False


def read_health_file() -> Optional[Dict[str, Any]]:
    """Read and parse the health file."""
    try:
        if not HEALTH_FILE.exists():
            return None
        content = HEALTH_FILE.read_text()
        return json.loads(content)
    except Exception as e:
        logger.debug("Error reading health file: %s", e)
        return None


def get_collector_status() -> CollectorStatus:
    """Get comprehensive status of the news collector."""
    status = CollectorStatus()

    # Check if process is running
    pid = find_collector_pid()
    status.pid = pid
    status.is_running = pid is not None

    # Read health file
    health = read_health_file()
    if health:
        status.health_status = health.get("status", "unknown")
        status.uptime_seconds = health.get("uptime_seconds", 0)
        status.cycles = health.get("cycles", 0)
        status.articles_processed = health.get("articles_processed", 0)
        status.consecutive_failures = health.get("consecutive_failures", 0)
        status.error = health.get("error")

        # Parse last cycle time
        last_cycle_str = health.get("last_cycle")
        if last_cycle_str:
            try:
                status.last_cycle = datetime.fromisoformat(last_cycle_str)
            except Exception:
                pass

        # Calculate health file age
        timestamp_str = health.get("timestamp")
        if timestamp_str:
            try:
                timestamp = datetime.fromisoformat(timestamp_str)
                status.health_file_age = (datetime.now() - timestamp).total_seconds()
            except Exception:
                pass
    else:
        status.health_status = "no_health_file"

    return status


def is_collector_healthy(status: CollectorStatus, config: WatchdogConfig) -> bool:
    """Determine if the collector is healthy based on status."""
    # Must be running
    if not status.is_running:
        logger.warning("Collector is NOT running")
        return False

    # Health file must exist and be recent
    if status.health_file_age is None:
        logger.warning("No health file found")
        return False

    if status.health_file_age > config.max_stale_seconds:
        logger.warning(
            "Health file is stale (%.0fs old, max %.0fs)",
            status.health_file_age, config.max_stale_seconds
        )
        return False

    # Check health status
    if status.health_status in ("error", "failed"):
        logger.warning("Collector health status: %s", status.health_status)
        return False

    # Check consecutive failures
    if status.consecutive_failures >= 5:
        logger.warning(
            "Too many consecutive failures: %d",
            status.consecutive_failures
        )
        return False

    return True


def stop_collector(pid: int) -> bool:
    """Stop the collector gracefully, then forcefully if needed."""
    logger.info("Stopping collector (PID %d)...", pid)

    try:
        # Check if it's a zombie first - zombies need to be reaped, not killed
        if _is_zombie(pid):
            logger.info("Process is a zombie, attempting to reap...")
            try:
                os.waitpid(pid, os.WNOHANG)
                logger.info("Zombie process reaped successfully")
            except ChildProcessError:
                # Not our child, but zombie should clear eventually
                logger.info("Zombie not our child, but proceeding anyway")
            return True

        # Try graceful shutdown first
        os.kill(pid, signal.SIGTERM)
        time.sleep(5)

        if _is_process_running(pid):
            # Force kill
            logger.warning("Collector didn't stop gracefully, sending SIGKILL")
            os.kill(pid, signal.SIGKILL)
            time.sleep(2)

        # Check for zombie after kill
        if _is_zombie(pid):
            logger.info("Process became zombie after kill, reaping...")
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            return True

        if _is_process_running(pid):
            logger.error("Failed to stop collector")
            return False

        logger.info("Collector stopped successfully")
        return True

    except Exception as e:
        logger.error("Error stopping collector: %s", e)
        return False


def start_collector(config: WatchdogConfig) -> Optional[int]:
    """Start the news collector and return its PID."""
    logger.info("Starting news collector...")

    # Create log file with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"news_collector_{timestamp}.log"

    # Build command
    python_path = PROJECT_ROOT / "venv" / "bin" / "python3"
    script_path = PROJECT_ROOT / "scripts" / "news_collector.py"

    cmd = [
        str(python_path),
        str(script_path),
        "--symbols", config.collector_symbols,
        "--interval", str(config.collector_interval),
    ]

    try:
        # Start the collector
        with open(log_file, 'w') as log_fh:
            process = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                start_new_session=True,  # Detach from parent
            )

        pid = process.pid

        # Write PID file
        PID_FILE.write_text(str(pid))

        # Give it a moment to start
        time.sleep(3)

        if _is_process_running(pid):
            logger.info("Collector started successfully (PID %d)", pid)
            logger.info("Log file: %s", log_file)
            return pid
        else:
            logger.error("Collector failed to start")
            return None

    except Exception as e:
        logger.error("Error starting collector: %s", e)
        return None


def restart_collector(config: WatchdogConfig) -> bool:
    """Restart the news collector."""
    logger.info("=" * 60)
    logger.info("RESTARTING NEWS COLLECTOR")
    logger.info("=" * 60)

    # Stop existing collector if running
    pid = find_collector_pid()
    if pid:
        if not stop_collector(pid):
            logger.error("Failed to stop existing collector")
            return False

    # Clean up PID file
    if PID_FILE.exists():
        PID_FILE.unlink()

    # Start new collector
    new_pid = start_collector(config)
    if new_pid:
        logger.info("Collector restarted successfully")
        return True
    else:
        logger.error("Failed to start collector")
        return False


def run_check(config: WatchdogConfig) -> bool:
    """Run a single health check. Returns True if healthy."""
    status = get_collector_status()

    logger.info("-" * 40)
    logger.info("Health Check at %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("  PID: %s", status.pid or "NOT RUNNING")
    logger.info("  Status: %s", status.health_status)
    logger.info("  Cycles: %d", status.cycles)
    logger.info("  Articles: %d", status.articles_processed)
    if status.health_file_age is not None:
        logger.info("  Health age: %.0fs", status.health_file_age)
    if status.consecutive_failures > 0:
        logger.info("  Failures: %d", status.consecutive_failures)

    return is_collector_healthy(status, config)


def run_daemon(config: WatchdogConfig):
    """Run the watchdog as a daemon."""
    logger.info("=" * 60)
    logger.info("WATCHDOG SERVICE STARTED")
    logger.info("=" * 60)
    logger.info("Check interval: %ds", config.check_interval)
    logger.info("Max stale: %ds", config.max_stale_seconds)
    logger.info("Restart cooldown: %ds", config.restart_cooldown)

    restart_attempts = 0
    last_restart_time = None

    while True:
        try:
            is_healthy = run_check(config)

            if is_healthy:
                logger.info("  Result: HEALTHY")
                restart_attempts = 0
            else:
                logger.warning("  Result: UNHEALTHY")

                # Check if we should restart
                can_restart = True
                if last_restart_time:
                    elapsed = (datetime.now() - last_restart_time).total_seconds()
                    if elapsed < config.restart_cooldown:
                        logger.info(
                            "  Waiting for cooldown (%.0fs remaining)",
                            config.restart_cooldown - elapsed
                        )
                        can_restart = False

                if can_restart and restart_attempts < config.max_restart_attempts:
                    restart_attempts += 1
                    logger.info(
                        "  Restart attempt %d/%d",
                        restart_attempts, config.max_restart_attempts
                    )
                    if restart_collector(config):
                        last_restart_time = datetime.now()
                    else:
                        logger.error("  Restart failed!")

                elif restart_attempts >= config.max_restart_attempts:
                    logger.error(
                        "  Max restart attempts reached (%d). Manual intervention required.",
                        config.max_restart_attempts
                    )

            time.sleep(config.check_interval)

        except KeyboardInterrupt:
            logger.info("Watchdog stopped by user")
            break
        except Exception as e:
            logger.error("Watchdog error: %s", e)
            time.sleep(config.check_interval)


def main():
    parser = argparse.ArgumentParser(description="News Collector Watchdog")

    parser.add_argument(
        "--daemon", action="store_true",
        help="Run as a daemon (continuous monitoring)"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Run a single health check"
    )
    parser.add_argument(
        "--restart", action="store_true",
        help="Force restart the collector"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show collector status and exit"
    )
    parser.add_argument(
        "--check-interval", type=int, default=60,
        help="Seconds between checks (default: 60)"
    )
    parser.add_argument(
        "--max-stale", type=int, default=600,
        help="Max health file age in seconds (default: 600)"
    )
    parser.add_argument(
        "--symbols", type=str,
        default="XAU,XAG,TSLA,NVDA,AMD,COIN,AAPL,GOOGL,MSFT,AMZN,META",
        help="Symbols for collector"
    )
    parser.add_argument(
        "--interval", type=int, default=5,
        help="Collector fetch interval in minutes"
    )

    args = parser.parse_args()

    # Build config
    config = WatchdogConfig()
    config.check_interval = args.check_interval
    config.max_stale_seconds = args.max_stale
    config.collector_symbols = args.symbols
    config.collector_interval = args.interval

    # Ensure logs directory exists
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    if args.status:
        status = get_collector_status()
        print("\n=== News Collector Status ===")
        print(f"Running: {'YES' if status.is_running else 'NO'}")
        if status.pid:
            print(f"PID: {status.pid}")
        print(f"Health: {status.health_status}")
        print(f"Cycles: {status.cycles}")
        print(f"Articles: {status.articles_processed}")
        if status.uptime_seconds:
            print(f"Uptime: {timedelta(seconds=int(status.uptime_seconds))}")
        if status.last_cycle:
            print(f"Last cycle: {status.last_cycle}")
        if status.health_file_age is not None:
            print(f"Health file age: {status.health_file_age:.0f}s")
        if status.error:
            print(f"Last error: {status.error}")
        print()

    elif args.restart:
        restart_collector(config)

    elif args.check:
        is_healthy = run_check(config)
        sys.exit(0 if is_healthy else 1)

    elif args.daemon:
        run_daemon(config)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
