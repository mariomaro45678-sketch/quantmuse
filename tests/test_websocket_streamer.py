"""
Unit tests for WebsocketStreamer.
Focuses on mock mode to verify callback registry and synthetic data flow.
"""

import pytest
import asyncio
import time
from unittest.mock import MagicMock

from data_service.realtime.websocket_streamer import WebsocketStreamer

@pytest.mark.asyncio
async def test_mock_ticker_streaming():
    """Verify that ticker callbacks are triggered in mock mode."""
    streamer = WebsocketStreamer(mode="mock")
    received_data = []

    def on_ticker(symbol, price):
        received_data.append((symbol, price))

    streamer.subscribe_ticker("XAU", on_ticker)
    
    await streamer.start()
    # Wait for at least 2 ticks
    await asyncio.sleep(2.5)
    await streamer.stop()

    assert len(received_data) >= 2
    assert received_data[0][0] == "XAU"
    assert isinstance(received_data[0][1], float)

@pytest.mark.asyncio
async def test_mock_trade_streaming():
    """Verify that trade callbacks are triggered in mock mode."""
    streamer = WebsocketStreamer(mode="mock")
    received_trades = []

    def on_trade(symbol, trades):
        received_trades.extend(trades)

    streamer.subscribe_trades("XAU", on_trade)
    
    # Force high probability of trades for test
    import random
    original_random = random.random
    random.random = lambda: 0.01 # Always trigger trade
    
    await streamer.start()
    await asyncio.sleep(1.5)
    await streamer.stop()
    
    random.random = original_random

    assert len(received_trades) >= 1
    assert received_trades[0]['coin'] == "XAU"
    assert "px" in received_trades[0]

@pytest.mark.asyncio
async def test_callback_registry_isolation():
    """Ensure callbacks for one symbol don't fire for another."""
    streamer = WebsocketStreamer(mode="mock")
    xau_data = []
    tsla_data = []

    streamer.subscribe_ticker("XAU", lambda s, p: xau_data.append(p))
    streamer.subscribe_ticker("TSLA", lambda s, p: tsla_data.append(p))

    # Manually trigger updates to test registry logic
    streamer._on_all_mids({
        "data": {
            "mids": {
                "XAU": "2050.0",
                "NVDA": "600.0" # Should ignore
            }
        }
    })

    assert len(xau_data) == 1
    assert xau_data[0] == 2050.0
    assert len(tsla_data) == 0
