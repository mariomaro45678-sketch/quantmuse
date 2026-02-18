"""
Health Check - Track system uptime and component status.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class HealthCheck:
    """
    Lightweight health monitoring for the trading system.
    Tracks uptime, API calls, orders, and WebSocket connections.
    """
    
    start_time: float = field(default_factory=time.time)
    last_api_call: Optional[float] = None
    last_order_placed: Optional[float] = None
    active_websocket_connections: int = 0
    total_api_calls: int = 0
    total_orders_placed: int = 0
    total_errors: int = 0
    
    def uptime_seconds(self) -> float:
        """Get system uptime in seconds."""
        return time.time() - self.start_time
    
    def uptime_formatted(self) -> str:
        """Get formatted uptime string."""
        seconds = int(self.uptime_seconds())
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    def record_api_call(self) -> None:
        """Record a successful API call."""
        self.last_api_call = time.time()
        self.total_api_calls += 1
    
    def record_order(self) -> None:
        """Record an order placement."""
        self.last_order_placed = time.time()
        self.total_orders_placed += 1
    
    def record_error(self) -> None:
        """Record an error."""
        self.total_errors += 1
    
    def increment_websocket_connections(self) -> None:
        """Increment active WebSocket connection count."""
        self.active_websocket_connections += 1
    
    def decrement_websocket_connections(self) -> None:
        """Decrement active WebSocket connection count."""
        self.active_websocket_connections = max(0, self.active_websocket_connections - 1)
    
    def record_ws_connection(self, connected: bool) -> None:
        """Update active WebSocket connection status."""
        if connected:
            self.active_websocket_connections = max(1, self.active_websocket_connections)
        else:
            self.active_websocket_connections = 0
    
    def to_dict(self) -> dict:
        """Convert health check to dictionary for API responses."""
        return {
            "uptime_seconds": self.uptime_seconds(),
            "uptime_formatted": self.uptime_formatted(),
            "last_api_call": datetime.fromtimestamp(self.last_api_call).isoformat() if self.last_api_call else None,
            "last_order_placed": datetime.fromtimestamp(self.last_order_placed).isoformat() if self.last_order_placed else None,
            "active_websocket_connections": self.active_websocket_connections,
            "total_api_calls": self.total_api_calls,
            "total_orders_placed": self.total_orders_placed,
            "total_errors": self.total_errors,
        }


# Global health check instance
_health: Optional[HealthCheck] = None


def get_health() -> HealthCheck:
    """Get the global HealthCheck instance."""
    global _health
    if _health is None:
        _health = HealthCheck()
    return _health
