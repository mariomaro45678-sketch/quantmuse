#!/usr/bin/env python3
"""
Test script for Economic Calendar module.

Tests:
1. Forex Factory scraping
2. Event caching
3. Trading multiplier logic
4. Pre-event and event window detection
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_service.ai.sources.economic_calendar import (
    EconomicCalendar,
    ForexFactoryScraper,
    EconomicEvent,
    EventImpact,
    get_economic_calendar,
)


def test_scraper():
    """Test the Forex Factory scraper directly."""
    print("\n" + "=" * 60)
    print("TEST 1: Forex Factory Scraper")
    print("=" * 60)

    scraper = ForexFactoryScraper()

    print("\nScraping current week...")
    events = scraper.scrape_week(0)

    if events:
        print(f"Successfully scraped {len(events)} events")
        print("\nSample events:")
        for event in events[:5]:
            print(f"  - {event.datetime_utc.strftime('%Y-%m-%d %H:%M')} | "
                  f"{event.impact.value.upper():8} | {event.event_name}")
    else:
        print("WARNING: No events scraped. Site may be blocking or format changed.")

    print("\nScraping next week...")
    next_week = scraper.scrape_week(1)
    print(f"Next week: {len(next_week)} events")

    return len(events) > 0 or len(next_week) > 0


def test_calendar_cache():
    """Test the calendar caching mechanism."""
    print("\n" + "=" * 60)
    print("TEST 2: Calendar Caching")
    print("=" * 60)

    # Create fresh calendar
    calendar = EconomicCalendar()

    print("\nForcing refresh...")
    count = calendar.refresh(force=True)
    print(f"Loaded {count} events")

    # Check cache was saved
    if calendar.cache_file.exists():
        size = calendar.cache_file.stat().st_size
        print(f"Cache file created: {calendar.cache_file} ({size} bytes)")
    else:
        print("WARNING: Cache file not created")

    # Test reload from cache
    calendar2 = EconomicCalendar()
    print(f"Reloaded {len(calendar2._events)} events from cache")

    return count > 0


def test_upcoming_events():
    """Test fetching upcoming events."""
    print("\n" + "=" * 60)
    print("TEST 3: Upcoming Events")
    print("=" * 60)

    calendar = get_economic_calendar()
    calendar.refresh(force=False)  # Use cache if fresh

    # Get next 24 hours
    upcoming_24h = calendar.get_upcoming_events(hours_ahead=24)
    print(f"\nEvents in next 24 hours: {len(upcoming_24h)}")
    for event in upcoming_24h:
        mins = int(event.minutes_until)
        print(f"  {event.datetime_utc.strftime('%a %H:%M')} | "
              f"{event.impact.value.upper():8} | {event.event_name} (in {mins} min)")

    # Get next 7 days
    upcoming_week = calendar.get_upcoming_events(hours_ahead=168)
    print(f"\nEvents in next 7 days: {len(upcoming_week)}")

    # Next high impact event
    next_high = calendar.get_next_high_impact_event()
    if next_high:
        print(f"\nNext HIGH/CRITICAL event:")
        print(f"  {next_high.event_name}")
        print(f"  {next_high.datetime_utc.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"  Impact: {next_high.impact.value.upper()}")
        print(f"  In: {int(next_high.minutes_until)} minutes ({next_high.minutes_until / 60:.1f} hours)")

    return True


def test_trading_multiplier():
    """Test the trading multiplier logic."""
    print("\n" + "=" * 60)
    print("TEST 4: Trading Multiplier")
    print("=" * 60)

    calendar = get_economic_calendar()

    multiplier, reason = calendar.get_trading_multiplier()
    print(f"\nCurrent multiplier: {multiplier:.2f}")
    if reason:
        print(f"Reason: {reason}")
    else:
        print("Reason: Normal trading (no upcoming high-impact events)")

    # Test scenarios with mock events
    print("\n--- Testing with mock events ---")

    # Create mock event 30 minutes from now (should trigger pre-event)
    mock_event_30m = EconomicEvent(
        id="test1",
        datetime_utc=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30),
        currency="USD",
        event_name="Mock FOMC (30min away)",
        impact=EventImpact.CRITICAL,
    )

    in_pre, _ = calendar.is_pre_event_window(mock_event_30m)
    in_event = calendar.is_event_window(mock_event_30m)
    print(f"\nEvent in 30 min:")
    print(f"  In pre-event window: {in_pre}")
    print(f"  In event window: {in_event}")

    # Create mock event 5 minutes from now (should trigger event window)
    mock_event_5m = EconomicEvent(
        id="test2",
        datetime_utc=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5),
        currency="USD",
        event_name="Mock NFP (5min away)",
        impact=EventImpact.HIGH,
    )

    in_pre2, _ = calendar.is_pre_event_window(mock_event_5m)
    in_event2 = calendar.is_event_window(mock_event_5m)
    print(f"\nEvent in 5 min:")
    print(f"  In pre-event window: {in_pre2}")
    print(f"  In event window: {in_event2}")

    # Create mock event that just happened (should trigger post-event)
    mock_event_past = EconomicEvent(
        id="test3",
        datetime_utc=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5),
        currency="USD",
        event_name="Mock CPI (5min ago)",
        impact=EventImpact.HIGH,
    )

    in_pre3, _ = calendar.is_pre_event_window(mock_event_past)
    in_event3 = calendar.is_event_window(mock_event_past)
    print(f"\nEvent 5 min ago:")
    print(f"  In pre-event window: {in_pre3}")
    print(f"  In event window (post-event): {in_event3}")

    return True


def test_status_summary():
    """Test the status summary function."""
    print("\n" + "=" * 60)
    print("TEST 5: Status Summary")
    print("=" * 60)

    calendar = get_economic_calendar()
    summary = calendar.get_status_summary()

    print("\nCalendar Status:")
    print(f"  Last refresh: {summary.get('last_refresh', 'Never')}")
    print(f"  Total cached events: {summary.get('total_events_cached', 0)}")
    print(f"  Events in next 24h: {summary.get('upcoming_24h', 0)}")
    print(f"  Trading multiplier: {summary.get('trading_multiplier', 1.0):.2f}")
    print(f"  Trading reason: {summary.get('trading_reason', 'Normal')}")
    print(f"  In event window: {summary.get('in_event_window', False)}")

    if 'next_high_impact' in summary:
        nhi = summary['next_high_impact']
        print(f"\n  Next high-impact event:")
        print(f"    Event: {nhi['event']}")
        print(f"    Time: {nhi['datetime']}")
        print(f"    Impact: {nhi['impact']}")
        print(f"    In: {nhi['minutes_until']} min")

    if 'upcoming_events' in summary:
        print(f"\n  Upcoming events (next 24h):")
        for evt in summary['upcoming_events']:
            print(f"    {evt['time']} | {evt['impact'].upper():8} | {evt['event']}")

    return True


def main():
    print("=" * 60)
    print("ECONOMIC CALENDAR TEST SUITE")
    print("=" * 60)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = {
        "Scraper": test_scraper(),
        "Cache": test_calendar_cache(),
        "Upcoming Events": test_upcoming_events(),
        "Trading Multiplier": test_trading_multiplier(),
        "Status Summary": test_status_summary(),
    }

    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)
    for test_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {test_name}: {status}")

    all_passed = all(results.values())
    print("\n" + ("All tests passed!" if all_passed else "Some tests failed."))

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
