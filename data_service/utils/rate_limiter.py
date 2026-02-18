"""
Token Bucket Rate Limiter for API calls.

Provides async-compatible rate limiting to prevent API rate limit violations.
Uses the token bucket algorithm for smooth request distribution.
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TokenBucket:
    """
    Token bucket rate limiter with async support.

    Allows bursting up to `capacity` requests, then limits to `rate` requests/second.

    Example:
        limiter = TokenBucket(rate=5.0, capacity=10.0)
        await limiter.wait()  # Blocks if rate limit exceeded
        make_api_call()
    """
    rate: float  # tokens per second
    capacity: float  # max tokens (burst size)
    tokens: float = field(init=False)
    last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self):
        self.tokens = self.capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> float:
        """
        Try to acquire tokens. Returns wait time needed (0 if tokens available).

        Args:
            tokens: Number of tokens to acquire (default 1)

        Returns:
            Wait time in seconds (0 if tokens were available immediately)
        """
        async with self._lock:
            now = time.monotonic()

            # Refill tokens based on elapsed time
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0.0

            # Calculate wait time needed
            wait_time = (tokens - self.tokens) / self.rate
            return wait_time

    async def wait(self, tokens: int = 1):
        """
        Wait until tokens are available, then acquire them.

        Args:
            tokens: Number of tokens to acquire (default 1)
        """
        wait_time = await self.acquire(tokens)
        if wait_time > 0:
            logger.debug(f"Rate limiter: waiting {wait_time:.2f}s for tokens")
            await asyncio.sleep(wait_time)
            # Re-acquire after waiting
            await self.acquire(tokens)

    def available(self) -> float:
        """Return current number of available tokens (without locking)."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        return min(self.capacity, self.tokens + elapsed * self.rate)


# Global rate limiter instance (singleton pattern)
_rate_limiter: Optional[TokenBucket] = None
_limiter_lock = asyncio.Lock()


async def get_rate_limiter(rate: float = 5.0, capacity: float = 10.0) -> TokenBucket:
    """
    Get or create the global rate limiter instance.

    Args:
        rate: Requests per second (default 5.0 from config)
        capacity: Burst capacity (default 10.0, 2x rate)

    Returns:
        TokenBucket instance
    """
    global _rate_limiter

    if _rate_limiter is None:
        async with _limiter_lock:
            # Double-check after acquiring lock
            if _rate_limiter is None:
                _rate_limiter = TokenBucket(rate=rate, capacity=capacity)
                logger.info(f"Rate limiter initialized: {rate} req/s, burst={capacity}")

    return _rate_limiter


def get_rate_limiter_sync(rate: float = 5.0, capacity: float = 10.0) -> TokenBucket:
    """
    Synchronous version for non-async contexts.
    Creates limiter on first call, returns existing instance after.
    """
    global _rate_limiter

    if _rate_limiter is None:
        _rate_limiter = TokenBucket(rate=rate, capacity=capacity)
        logger.info(f"Rate limiter initialized (sync): {rate} req/s, burst={capacity}")

    return _rate_limiter


def reset_rate_limiter():
    """Reset the global rate limiter (useful for testing)."""
    global _rate_limiter
    _rate_limiter = None
