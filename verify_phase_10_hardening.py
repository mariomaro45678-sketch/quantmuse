import logging
from data_service.utils.logging_config import setup_logging
from pathlib import Path
import os

def test_secret_redaction():
    print("\n[TEST] Testing Secret Redaction...")
    
    # Setup logging
    log_file = Path("logs/test_hardening.log")
    if log_file.exists():
        log_file.unlink()
        
    setup_logging(log_file="test_hardening.log", level="INFO")
    logger = logging.getLogger("RedactionTest")
    
    # Sensitive data
    wallet = "0x1234567890123456789012345678901234567890"
    api_key = "sk-abcdefghijklmnopqrstuvwxyz12345678"
    private_key = "a" * 64
    
    logger.info(f"Connecting to wallet {wallet}")
    logger.info(f"Using API key: {api_key}")
    logger.info(f"Private key is {private_key}")
    
    # Verify file content
    with open(log_file, "r") as f:
        content = f.read()
        
    print(f"Log content preview:\n{content}")
    
    success = True
    if wallet in content:
        print("❌ Wallet not redacted!")
        success = False
    if api_key in content:
        print("❌ API key not redacted!")
        success = False
    if private_key in content:
        print("❌ Private key not redacted!")
        success = False
    
    if success:
        print("✅ Secret redaction works!")
    else:
        print("❌ Secret redaction FAILED!")
        
    return success

if __name__ == "__main__":
    test_secret_redaction()
