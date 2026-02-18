"""
Logging configuration module.
Sets up file and console handlers with rotation.
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional, List


class SecretRedactor(logging.Filter):
    """Filter to redact sensitive information like wallet addresses and API keys."""
    
    def __init__(self, patterns: Optional[List[str]] = None):
        super().__init__()
        import re
        self.patterns = patterns or [
            r'0x[a-fA-F0-9]{40}',      # Ethereum/Hyperliquid wallet
            r'sk-[a-zA-Z0-9]{20,}',    # OpenAI or generic secret keys
            r'[a-fA-F0-9]{64}',        # Private keys/hashes
        ]
        self.regex = re.compile('|'.join(f'({p})' for p in self.patterns))

    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = self.regex.sub('[REDACTED]', record.msg)
        if record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    new_args.append(self.regex.sub('[REDACTED]', arg))
                else:
                    new_args.append(arg)
            record.args = tuple(new_args)
        return True


def setup_logging(
    log_dir: Path = Path("logs"),
    log_file: str = "app.log",
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5
) -> None:
    """
    Configure Python logging with file rotation and console output.
    """
    # Create logs directory if it doesn't exist
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    console_formatter = logging.Formatter(
        "%(levelname)s - %(name)s - %(message)s"
    )
    
    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / log_file,
        maxBytes=max_bytes,
        backupCount=backup_count
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper()))
    console_handler.setFormatter(console_formatter)
    
    # Add SecretRedactor filter
    redactor = SecretRedactor()
    file_handler.addFilter(redactor)
    console_handler.addFilter(redactor)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()
    
    # Add handlers
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Set levels for noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    root_logger.info("Logging system initialized (with secret redaction)")


# Initialize logging on module import
setup_logging()
