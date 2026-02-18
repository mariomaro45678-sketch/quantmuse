"""
Configuration Loader - Single source of truth for all system configuration.
Reads .env first, then overlays JSON configs with environment variable substitution.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


logger = logging.getLogger(__name__)


class HyperliquidConfig(BaseModel):
    """Hyperliquid API configuration."""
    network: str
    wallet_address: str
    secret_key: str
    api_base_url: str
    ws_url: str
    max_leverage_per_asset: int
    max_portfolio_leverage: int
    max_position_pct: float
    max_daily_loss_pct: float
    rate_limit_requests_per_second: int = 5
    request_timeout_seconds: int = 30


class AssetConfig(BaseModel):
    """Individual asset configuration."""
    symbol: str
    display_name: str
    asset_class: str
    tick_size: float
    min_order_size: float
    max_leverage: int
    correlation_group: str


class ConfigLoader:
    """
    Centralized configuration loader.
    Reads and parses all JSON configs with environment variable substitution.
    """
    
    _instance: Optional['ConfigLoader'] = None
    
    def __new__(cls):
        """Singleton pattern - only one ConfigLoader instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize configuration loader."""
        if self._initialized:
            return
            
        # Load environment variables
        load_dotenv()
        
        # Set config directory
        if config_dir is None:
            config_dir = Path(__file__).parent.parent.parent / "config"
        self.config_dir = Path(config_dir)
        
        # Load all configurations
        self._hyperliquid: Optional[Dict[str, Any]] = None
        self._assets: Optional[Dict[str, Any]] = None
        self._strategies: Optional[Dict[str, Any]] = None
        self._risk: Optional[Dict[str, Any]] = None
        self._news_sources: Optional[Dict[str, Any]] = None
        
        self._initialized = True
        logger.info(f"ConfigLoader initialized with config_dir: {self.config_dir}")
    
    def _substitute_env_vars(self, value: Any) -> Any:
        """
        Recursively substitute ${VAR_NAME} placeholders with environment variables.
        """
        if isinstance(value, str):
            # Match ${VAR_NAME} pattern
            pattern = r'\$\{([^}]+)\}'
            matches = re.findall(pattern, value)
            for var_name in matches:
                env_value = os.getenv(var_name, "")
                value = value.replace(f"${{{var_name}}}", env_value)
            return value
        elif isinstance(value, dict):
            return {k: self._substitute_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._substitute_env_vars(item) for item in value]
        else:
            return value
    
    def _load_json(self, filename: str) -> Dict[str, Any]:
        """Load and parse JSON file with environment variable substitution."""
        filepath = self.config_dir / filename
        
        if not filepath.exists():
            logger.warning(f"Config file not found: {filepath}")
            return {}
        
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            # Substitute environment variables
            data = self._substitute_env_vars(data)
            
            logger.debug(f"Loaded config from {filename}")
            return data
        except Exception as e:
            logger.error(f"Failed to load {filename}: {e}")
            return {}
    
    @property
    def hyperliquid(self) -> Dict[str, Any]:
        """Get Hyperliquid configuration."""
        if self._hyperliquid is None:
            self._hyperliquid = self._load_json("hyperliquid_config.json")
        return self._hyperliquid
    
    @property
    def assets(self) -> Dict[str, Any]:
        """Get assets configuration."""
        if self._assets is None:
            self._assets = self._load_json("assets.json")
        return self._assets
    
    @property
    def strategies(self) -> Dict[str, Any]:
        """Get strategies configuration."""
        if self._strategies is None:
            self._strategies = self._load_json("strategies.json")
        return self._strategies
    
    @property
    def risk(self) -> Dict[str, Any]:
        """Get risk configuration."""
        if self._risk is None:
            self._risk = self._load_json("risk_config.json")
        return self._risk
    
    @property
    def news_sources(self) -> Dict[str, Any]:
        """Get news sources configuration."""
        if self._news_sources is None:
            self._news_sources = self._load_json("news_sources.json")
        return self._news_sources
    
    def get_asset(self, symbol: str) -> Optional[AssetConfig]:
        """Get configuration for a specific asset."""
        assets_list = self.assets.get("assets", [])
        for asset_data in assets_list:
            if asset_data.get("symbol") == symbol:
                return AssetConfig(**asset_data)
        return None
    
    def get_all_assets(self) -> list[AssetConfig]:
        """Get all asset configurations."""
        assets_list = self.assets.get("assets", [])
        return [AssetConfig(**asset_data) for asset_data in assets_list]
    
    def get_strategy_config(self, strategy_name: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific strategy."""
        strategies = self.strategies.get("strategies", {})
        return strategies.get(strategy_name)
    
    def is_mock_mode(self) -> bool:
        """Check if running in mock mode (no API credentials)."""
        network = self.hyperliquid.get("network", "")
        secret_key = self.hyperliquid.get("secret_key", "")
        
        return network == "mock" or not secret_key or secret_key.startswith("your_")


# Global config instance
_config: Optional[ConfigLoader] = None


def get_config() -> ConfigLoader:
    """Get the global ConfigLoader instance."""
    global _config
    if _config is None:
        _config = ConfigLoader()
    return _config
