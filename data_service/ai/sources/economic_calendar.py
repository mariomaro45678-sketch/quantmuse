"""
Economic Calendar Module - Forex Factory Scraper

Fetches high-impact economic events to adjust trading behavior:
- 1 hour before HIGH impact event: reduce position sizes 50%
- During event window (15 min): no new entries
- After event: normal trading resumes

Free data source: Forex Factory (scraping)

Events tracked:
- FOMC meetings (8/year) - CRITICAL
- CPI/PPI releases - HIGH
- NFP (Non-Farm Payrolls, first Friday) - HIGH
- GDP releases - HIGH
- Fed/ECB speeches - MEDIUM
"""

import logging
import re
import json
import sqlite3
import random
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from enum import Enum
import hashlib

import requests
import cloudscraper
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def load_proxies(proxy_file: Optional[Path] = None) -> List[Dict[str, str]]:
    """
    Load proxy list from file.

    Expected format per line: username:password:hostname:port
    """
    if proxy_file is None:
        proxy_file = Path(__file__).parent.parent.parent.parent / "Sticky_proxies_us.md"

    proxies = []
    if not proxy_file.exists():
        return proxies

    try:
        with open(proxy_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('Format'):
                    continue

                parts = line.split(':')
                if len(parts) >= 4:
                    username = parts[0]
                    password = parts[1]
                    host = parts[2]
                    port = parts[3]

                    proxy_url = f"http://{username}:{password}@{host}:{port}"
                    proxies.append({
                        'http': proxy_url,
                        'https': proxy_url,
                    })
    except Exception as e:
        logger.warning(f"Could not load proxies: {e}")

    return proxies


# Load proxies at module level
PROXY_LIST = load_proxies()
logger.info(f"Loaded {len(PROXY_LIST)} residential proxies")


class EventImpact(Enum):
    """Impact level of economic event."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"  # FOMC, NFP


@dataclass
class EconomicEvent:
    """Represents a single economic calendar event."""
    id: str
    datetime_utc: datetime
    currency: str
    event_name: str
    impact: EventImpact
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d['datetime_utc'] = self.datetime_utc.isoformat()
        d['impact'] = self.impact.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'EconomicEvent':
        d['datetime_utc'] = datetime.fromisoformat(d['datetime_utc'])
        d['impact'] = EventImpact(d['impact'])
        return cls(**d)

    @property
    def is_high_impact(self) -> bool:
        return self.impact in (EventImpact.HIGH, EventImpact.CRITICAL)

    @property
    def time_until(self) -> timedelta:
        return self.datetime_utc - datetime.now(timezone.utc).replace(tzinfo=None)

    @property
    def minutes_until(self) -> float:
        return self.time_until.total_seconds() / 60


class ForexFactoryScraper:
    """
    Scrapes economic calendar from Forex Factory.

    Uses browser-like headers and residential proxies to avoid blocking.
    Caches results to minimize requests.
    """

    BASE_URL = "https://www.forexfactory.com/calendar"

    # Browser-like headers
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }

    # Map impact colors/classes to our levels
    IMPACT_MAP = {
        'high': EventImpact.HIGH,
        'medium': EventImpact.MEDIUM,
        'low': EventImpact.LOW,
        'red': EventImpact.HIGH,
        'orange': EventImpact.MEDIUM,
        'yellow': EventImpact.LOW,
        'holiday': EventImpact.LOW,
    }

    # Events that are CRITICAL (beyond just HIGH)
    CRITICAL_EVENTS = [
        'fomc',
        'federal funds rate',
        'interest rate decision',
        'non-farm payrolls',
        'nonfarm payrolls',
        'employment change',  # NFP equivalent
    ]

    def __init__(self, timeout: int = 30, use_proxy: bool = True):
        self.timeout = timeout
        self.use_proxy = use_proxy and len(PROXY_LIST) > 0

        # Use cloudscraper to bypass Cloudflare protection
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )
        self.scraper.headers.update(self.HEADERS)

        if self.use_proxy:
            logger.info(f"ForexFactoryScraper initialized with {len(PROXY_LIST)} proxies")

    def _get_random_proxy(self) -> Optional[Dict[str, str]]:
        """Get a random proxy from the pool."""
        if not PROXY_LIST:
            return None
        return random.choice(PROXY_LIST)

    def _fetch_with_retry(self, url: str, max_retries: int = 3) -> Optional[requests.Response]:
        """Fetch URL with cloudscraper, proxy rotation and retries."""
        for attempt in range(max_retries):
            proxy = self._get_random_proxy() if self.use_proxy else None

            try:
                response = self.scraper.get(
                    url,
                    proxies=proxy,
                    timeout=self.timeout
                )
                response.raise_for_status()
                logger.debug(f"Successfully fetched {url}")
                return response

            except Exception as e:
                logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    # Try a different proxy
                    import time
                    time.sleep(1)  # Brief pause before retry
                    continue

        return None

    def _parse_impact(self, row) -> EventImpact:
        """Extract impact level from row."""
        # Look for impact cell (class is calendar__impact, not calendar__cell--impact)
        impact_cell = row.find('td', class_='calendar__impact')
        if impact_cell:
            span = impact_cell.find('span')
            if span:
                classes = span.get('class', [])
                class_str = ' '.join(classes).lower()

                # Forex Factory uses icon--ff-impact-red/ora/yel
                if 'impact-red' in class_str:
                    return EventImpact.HIGH
                elif 'impact-ora' in class_str:
                    return EventImpact.MEDIUM
                elif 'impact-yel' in class_str:
                    return EventImpact.LOW

                # Fallback to generic color matching
                for cls in classes:
                    cls_lower = cls.lower()
                    if 'high' in cls_lower or 'red' in cls_lower:
                        return EventImpact.HIGH
                    elif 'medium' in cls_lower or 'orange' in cls_lower or 'ora' in cls_lower:
                        return EventImpact.MEDIUM
                    elif 'low' in cls_lower or 'yellow' in cls_lower or 'yel' in cls_lower:
                        return EventImpact.LOW

        return EventImpact.LOW

    def _parse_datetime(self, date_str: str, time_str: str) -> Optional[datetime]:
        """Parse date and time strings into datetime."""
        try:
            # Clean up strings
            date_str = date_str.strip()
            time_str = time_str.strip()

            if not date_str or not time_str:
                return None

            # Forex Factory uses formats like "Mon Jan 15" or "Jan 15"
            # and times like "8:30am" or "Tentative" or "All Day"
            if 'tentative' in time_str.lower() or 'all day' in time_str.lower():
                time_str = "12:00am"  # Default to midnight

            # Get current year
            year = datetime.now().year

            # Try various date formats
            date_formats = [
                f"%a %b %d {year}",  # Mon Jan 15 2026
                f"%b %d {year}",      # Jan 15 2026
            ]

            parsed_date = None
            for fmt in date_formats:
                try:
                    # Add year to date string
                    full_date = f"{date_str} {year}"
                    parsed_date = datetime.strptime(full_date, fmt)
                    break
                except ValueError:
                    continue

            if not parsed_date:
                # Try to extract month and day
                months = {
                    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
                }
                for month_name, month_num in months.items():
                    if month_name in date_str.lower():
                        day_match = re.search(r'\d+', date_str)
                        if day_match:
                            day = int(day_match.group())
                            parsed_date = datetime(year, month_num, day)
                            break

            if not parsed_date:
                return None

            # Parse time (format: "8:30am" or "10:00pm")
            time_match = re.match(r'(\d+):(\d+)(am|pm)?', time_str.lower())
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))
                ampm = time_match.group(3)

                if ampm == 'pm' and hour != 12:
                    hour += 12
                elif ampm == 'am' and hour == 12:
                    hour = 0

                parsed_date = parsed_date.replace(hour=hour, minute=minute)

            # Forex Factory times are in ET (Eastern Time)
            # Convert to UTC (ET is UTC-5, or UTC-4 during DST)
            # For simplicity, assume UTC-5
            parsed_date = parsed_date + timedelta(hours=5)

            return parsed_date

        except Exception as e:
            logger.debug(f"Could not parse datetime: {date_str} {time_str}: {e}")
            return None

    def _is_critical_event(self, event_name: str) -> bool:
        """Check if event is CRITICAL (FOMC, NFP, etc.)."""
        name_lower = event_name.lower()
        return any(critical in name_lower for critical in self.CRITICAL_EVENTS)

    def scrape_week(self, week_offset: int = 0) -> List[EconomicEvent]:
        """
        Scrape a week of economic calendar data.

        Args:
            week_offset: 0 = current week, 1 = next week, -1 = last week

        Returns:
            List of EconomicEvent objects
        """
        events = []

        # Build URL with week parameter
        if week_offset == 0:
            url = f"{self.BASE_URL}?week=this"
        elif week_offset > 0:
            url = f"{self.BASE_URL}?week=next"
        else:
            url = f"{self.BASE_URL}?week=last"

        response = self._fetch_with_retry(url)
        if not response:
            logger.error(f"Failed to fetch Forex Factory calendar after retries")
            return events

        soup = BeautifulSoup(response.text, 'html.parser')

        # Find calendar table
        table = soup.find('table', class_='calendar__table')
        if not table:
            logger.warning("Could not find calendar table")
            return events

        # Track current date as we iterate (dates span multiple rows)
        current_date = ""
        current_time = ""

        # Find all calendar rows
        rows = table.find_all('tr', class_='calendar__row')

        for row in rows:
            try:
                # Check if this row has a date (class is calendar__date, not calendar__cell--date)
                date_cell = row.find('td', class_='calendar__date')
                if date_cell:
                    # Date text is directly in the cell or in a span
                    current_date = date_cell.get_text(strip=True)

                # Check if this row has a time
                time_cell = row.find('td', class_='calendar__time')
                if time_cell:
                    current_time = time_cell.get_text(strip=True)

                # Get currency
                currency_cell = row.find('td', class_='calendar__currency')
                if not currency_cell:
                    continue
                currency = currency_cell.get_text(strip=True)

                # Only care about USD events for now (most impactful for our assets)
                if currency.upper() != 'USD':
                    continue

                # Get event name
                event_cell = row.find('td', class_='calendar__event')
                if not event_cell:
                    continue
                event_name = event_cell.get_text(strip=True)

                if not event_name:
                    continue

                # Get impact
                impact = self._parse_impact(row)

                # Upgrade to CRITICAL if it's a key event
                if self._is_critical_event(event_name):
                    impact = EventImpact.CRITICAL

                # Skip low impact events
                if impact == EventImpact.LOW:
                    continue

                # Parse datetime
                event_dt = self._parse_datetime(current_date, current_time)
                if not event_dt:
                    continue

                # Get forecast, previous, actual
                forecast = ""
                previous = ""
                actual = ""

                forecast_cell = row.find('td', class_='calendar__forecast')
                if forecast_cell:
                    forecast = forecast_cell.get_text(strip=True)

                previous_cell = row.find('td', class_='calendar__previous')
                if previous_cell:
                    previous = previous_cell.get_text(strip=True)

                actual_cell = row.find('td', class_='calendar__actual')
                if actual_cell:
                    actual = actual_cell.get_text(strip=True)

                # Create unique ID
                id_str = f"{event_dt.isoformat()}_{currency}_{event_name}"
                event_id = hashlib.md5(id_str.encode()).hexdigest()[:12]

                event = EconomicEvent(
                    id=event_id,
                    datetime_utc=event_dt,
                    currency=currency,
                    event_name=event_name,
                    impact=impact,
                    forecast=forecast or None,
                    previous=previous or None,
                    actual=actual or None,
                )
                events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing row: {e}")
                continue

        logger.info(f"Scraped {len(events)} events from Forex Factory (week={week_offset})")
        return events


class EconomicCalendar:
    """
    Main economic calendar interface.

    Provides cached access to economic events and trading adjustments.
    """

    # Cache settings
    CACHE_TTL_HOURS = 6

    # Trading adjustments
    PRE_EVENT_WINDOW_MINUTES = 60  # Reduce exposure 1 hour before
    EVENT_WINDOW_MINUTES = 15      # No new entries during event
    POST_EVENT_WINDOW_MINUTES = 15 # Transition period after event

    # Position size multipliers
    MULTIPLIERS = {
        EventImpact.CRITICAL: 0.25,  # 75% reduction
        EventImpact.HIGH: 0.50,      # 50% reduction
        EventImpact.MEDIUM: 0.75,    # 25% reduction
        EventImpact.LOW: 1.0,        # No change
    }

    def __init__(self, db_path: Optional[str] = None, cache_dir: Optional[Path] = None):
        """
        Initialize economic calendar.

        Args:
            db_path: Path to SQLite database for caching
            cache_dir: Directory for JSON cache files
        """
        self.scraper = ForexFactoryScraper()

        # Setup cache
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = Path(__file__).parent.parent.parent.parent / "data" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "economic_calendar.json"

        # In-memory cache
        self._events: List[EconomicEvent] = []
        self._last_refresh: Optional[datetime] = None

        # Load from cache if available
        self._load_cache()

    def _load_cache(self):
        """Load events from cache file."""
        if not self.cache_file.exists():
            return

        try:
            with open(self.cache_file, 'r') as f:
                data = json.load(f)

            self._last_refresh = datetime.fromisoformat(data.get('last_refresh', ''))
            self._events = [EconomicEvent.from_dict(e) for e in data.get('events', [])]

            logger.info(f"Loaded {len(self._events)} events from cache "
                       f"(last refresh: {self._last_refresh})")
        except Exception as e:
            logger.warning(f"Could not load cache: {e}")
            self._events = []
            self._last_refresh = None

    def _save_cache(self):
        """Save events to cache file."""
        try:
            data = {
                'last_refresh': datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                'events': [e.to_dict() for e in self._events]
            }
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved {len(self._events)} events to cache")
        except Exception as e:
            logger.warning(f"Could not save cache: {e}")

    def _needs_refresh(self) -> bool:
        """Check if cache needs refreshing."""
        if not self._last_refresh:
            return True

        age = datetime.now(timezone.utc).replace(tzinfo=None) - self._last_refresh
        return age.total_seconds() > self.CACHE_TTL_HOURS * 3600

    def refresh(self, force: bool = False) -> int:
        """
        Refresh calendar data from Forex Factory.

        Args:
            force: Force refresh even if cache is fresh

        Returns:
            Number of events loaded
        """
        if not force and not self._needs_refresh():
            logger.debug("Cache is fresh, skipping refresh")
            return len(self._events)

        logger.info("Refreshing economic calendar...")

        # Scrape current and next week
        all_events = []
        all_events.extend(self.scraper.scrape_week(0))  # This week
        all_events.extend(self.scraper.scrape_week(1))  # Next week

        # Deduplicate by ID
        seen = set()
        unique_events = []
        for event in all_events:
            if event.id not in seen:
                seen.add(event.id)
                unique_events.append(event)

        # Sort by datetime
        unique_events.sort(key=lambda e: e.datetime_utc)

        self._events = unique_events
        self._last_refresh = datetime.now(timezone.utc).replace(tzinfo=None)
        self._save_cache()

        logger.info(f"Loaded {len(self._events)} unique events")
        return len(self._events)

    def get_upcoming_events(
        self,
        hours_ahead: int = 24,
        min_impact: EventImpact = EventImpact.MEDIUM
    ) -> List[EconomicEvent]:
        """
        Get upcoming economic events.

        Args:
            hours_ahead: How many hours ahead to look
            min_impact: Minimum impact level to include

        Returns:
            List of upcoming events, sorted by time
        """
        # Refresh if needed
        if self._needs_refresh():
            self.refresh()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now + timedelta(hours=hours_ahead)

        # Filter by time and impact
        impact_order = [EventImpact.LOW, EventImpact.MEDIUM, EventImpact.HIGH, EventImpact.CRITICAL]
        min_idx = impact_order.index(min_impact)

        upcoming = [
            e for e in self._events
            if now <= e.datetime_utc <= cutoff
            and impact_order.index(e.impact) >= min_idx
        ]

        return sorted(upcoming, key=lambda e: e.datetime_utc)

    def get_next_high_impact_event(self) -> Optional[EconomicEvent]:
        """Get the next HIGH or CRITICAL impact event."""
        upcoming = self.get_upcoming_events(hours_ahead=168, min_impact=EventImpact.HIGH)
        return upcoming[0] if upcoming else None

    def is_event_window(self, event: Optional[EconomicEvent] = None) -> bool:
        """
        Check if we're currently in an event window (no new entries).

        Args:
            event: Specific event to check, or None to check all upcoming

        Returns:
            True if in event window
        """
        if event:
            events_to_check = [event]
        else:
            events_to_check = self.get_upcoming_events(hours_ahead=2, min_impact=EventImpact.HIGH)

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for e in events_to_check:
            minutes_until = e.minutes_until

            # During event window (from -EVENT_WINDOW to +POST_EVENT_WINDOW)
            if -self.POST_EVENT_WINDOW_MINUTES <= minutes_until <= self.EVENT_WINDOW_MINUTES:
                return True

        return False

    def is_pre_event_window(self, event: Optional[EconomicEvent] = None) -> Tuple[bool, Optional[EconomicEvent]]:
        """
        Check if we're in the pre-event window (reduce exposure).

        Args:
            event: Specific event to check, or None to check all upcoming

        Returns:
            Tuple of (is_in_window, nearest_event)
        """
        if event:
            events_to_check = [event]
        else:
            events_to_check = self.get_upcoming_events(hours_ahead=2, min_impact=EventImpact.HIGH)

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for e in events_to_check:
            minutes_until = e.minutes_until

            # In pre-event window (from -PRE_EVENT to -EVENT_WINDOW)
            if self.EVENT_WINDOW_MINUTES < minutes_until <= self.PRE_EVENT_WINDOW_MINUTES:
                return True, e

        return False, None

    def get_trading_multiplier(self) -> Tuple[float, Optional[str]]:
        """
        Get the position size multiplier based on upcoming events.

        Returns:
            Tuple of (multiplier, reason_string)
        """
        # Refresh if needed
        if self._needs_refresh():
            try:
                self.refresh()
            except Exception as e:
                logger.warning(f"Could not refresh calendar: {e}")

        # Check for event window (no trading)
        if self.is_event_window():
            next_event = self.get_next_high_impact_event()
            event_name = next_event.event_name if next_event else "unknown"
            return 0.0, f"Event window: {event_name}"

        # Check for pre-event window
        in_pre_window, event = self.is_pre_event_window()
        if in_pre_window and event:
            multiplier = self.MULTIPLIERS.get(event.impact, 1.0)
            minutes = int(event.minutes_until)
            return multiplier, f"{event.event_name} in {minutes}min"

        return 1.0, None

    def get_status_summary(self) -> Dict:
        """Get a summary of calendar status for display/logging."""
        next_event = self.get_next_high_impact_event()
        multiplier, reason = self.get_trading_multiplier()

        upcoming_24h = self.get_upcoming_events(hours_ahead=24)

        summary = {
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "total_events_cached": len(self._events),
            "upcoming_24h": len(upcoming_24h),
            "trading_multiplier": multiplier,
            "trading_reason": reason,
            "in_event_window": self.is_event_window(),
        }

        if next_event:
            summary["next_high_impact"] = {
                "event": next_event.event_name,
                "datetime": next_event.datetime_utc.isoformat(),
                "impact": next_event.impact.value,
                "minutes_until": int(next_event.minutes_until),
            }

        if upcoming_24h:
            summary["upcoming_events"] = [
                {
                    "event": e.event_name,
                    "time": e.datetime_utc.strftime("%H:%M UTC"),
                    "impact": e.impact.value,
                    "minutes_until": int(e.minutes_until),
                }
                for e in upcoming_24h[:5]  # Top 5
            ]

        return summary


# Singleton instance
_calendar: Optional[EconomicCalendar] = None


def get_economic_calendar() -> EconomicCalendar:
    """Get or create the singleton economic calendar."""
    global _calendar
    if _calendar is None:
        _calendar = EconomicCalendar()
    return _calendar
