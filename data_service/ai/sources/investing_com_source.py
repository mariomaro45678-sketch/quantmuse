import logging
import random
import asyncio
import cloudscraper
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import List, Optional
import os
from data_service.ai.sources.base_source import BaseNewsSource, Article
import hashlib

logger = logging.getLogger(__name__)

class InvestingComSource(BaseNewsSource):
    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = config.get('base_url', 'https://www.investing.com/news')
        self.intervals = config.get('intervals_seconds', [30, 90])
        self.use_proxies = config.get('use_proxies', False)
        self.proxy_pool = self._load_proxies()
        
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            },
            interpreter='nodejs',
            delay=10
        )
        # Update headers to mimic real chrome
        self.scraper.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Ch-Ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-User': '?1'
        })
        self.current_proxy = None
        self.session_start_time = datetime.now()
        self.session_duration = timedelta(minutes=random.randint(10, 30)) # Sticky session 10-30m
        self._rotate_proxy()

    def get_source_name(self) -> str:
        return "Investing.com (Scraper)"

    def _load_proxies(self) -> List[dict]:
        pool_str = os.getenv('PROXY_POOL', '')
        if not pool_str:
            return []
        
        proxies = []
        # Expecting comma separated proxies: scheme://user:pass@host:port
        for p in pool_str.split(','):
            p = p.strip()
            if p:
                proxies.append({
                    "http": p,
                    "https": p
                })
        return proxies

    def _rotate_proxy(self):
        if not self.use_proxies or not self.proxy_pool:
            self.current_proxy = None
            return

        # Check if session expired or not set
        now = datetime.now()
        if not self.current_proxy or (now - self.session_start_time) > self.session_duration:
            self.current_proxy = random.choice(self.proxy_pool)
            self.session_start_time = now
            self.session_duration = timedelta(minutes=random.randint(10, 30))
            self.scraper.proxies = self.current_proxy
            logger.info(f"Rotated proxy. New session duration: {self.session_duration}")

    async def fetch_news(self, symbols: List[str], hours_back: int) -> List[Article]:
        """
        Scrape commodities and stock news.
        This is a blocking call, should be run in executor if async not supported natively by cloudscraper.
        """
        self._rotate_proxy()
        
        articles = []
        # Categories to scrape
        endpoints = [
            "/commodities-news", 
            "/stock-market-news"
        ]
        
        for endpoint in endpoints:
            url = f"{self.base_url}{endpoint}"
            for attempt in range(5): # Retry up to 5 times per endpoint
                try:
                    # Add jitter
                    await asyncio.sleep(random.uniform(2, 5))
                    
                    # Cloudscraper is synchronous, run in thread
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(None, self.scraper.get, url)
                    
                    if response.status_code != 200:
                        logger.warning(f"Failed to fetch {url}: {response.status_code}")
                        if response.status_code == 403:
                            logger.warning(f"Response Body Preview: {response.text[:500]}")
                        continue
                        continue
                    
                    new_articles = self._parse_html(response.text, endpoint)
                    articles.extend(new_articles)
                    break # Success, move to next endpoint
                    
                except Exception as e:
                    logger.error(f"Error scraping {url} (Attempt {attempt+1}/3): {e}")
                    self._rotate_proxy() # Rotate proxy and retry
                    await asyncio.sleep(2)

                
        return articles

    def _parse_html(self, html: str, category: str) -> List[Article]:
        soup = BeautifulSoup(html, 'html.parser')
        articles = []
        
        # Selectors might change, keep robust
        # Investing.com typically uses 'article' tag or specific classes for list items
        # Currently: div[data-test="article-item"] or similar.
        # Fallback to broad search if specific class fails.
        
        # Using a generic approach for the news list
        # Look for the main news list container. 
        # Often: section['id'] = 'leftColumn' -> div['class'] = 'largeTitle' -> article
        
        # Try finding articles by common structure
        items = soup.find_all('article')
        if not items:
            # Fallback for old design
            items = soup.select('div.largeTitle > article')
        
        for item in items:
            try:
                # Extract Title
                title_tag = item.find('a', class_='title') or item.find('a')
                if not title_tag:
                    continue
                title = title_tag.text.strip()
                link = title_tag.get('href')
                if link and not link.startswith('http'):
                    link = "https://www.investing.com" + link

                # Extract Timestamp (approximate) - often in 'span.date' or 'time'
                # Investing.com format: " - 5 hours ago" or "Nov 12, 2024"
                date_text = ""
                details = item.find('span', class_='articleDetails')
                if details:
                    date_span = details.find('span', class_='date')
                    if date_span:
                        date_text = date_span.text.strip().replace(' - ', '')
                
                # Parse date_text to datetime (simplified)
                # If "ago", subtract from now. If date, parse.
                published_at = self._parse_investing_date(date_text)

                # Generate ID
                aid = hashlib.md5((title + link).encode()).hexdigest()
                
                articles.append(Article(
                    id=aid,
                    symbol="GENERAL", # Will be filtered by NLP later
                    title=title,
                    content=title, # We only get title/snippet from list
                    source="Investing.com",
                    published_at=published_at,
                    url=link,
                    sentiment_score=None
                ))
            except Exception as e:
                continue
                
        return articles

    def _parse_investing_date(self, date_text: str) -> datetime:
        now = datetime.now()
        try:
            if 'min' in date_text:
                mins = int(date_text.split()[0])
                return now - timedelta(minutes=mins)
            elif 'hour' in date_text:
                hours = int(date_text.split()[0])
                return now - timedelta(hours=hours)
            else:
                return now # Default to now if unparseable
        except:
            return now

    async def start_stream(self, callback) -> None:
        """Poll scraper in a loop."""
        while True:
            try:
                articles = await self.fetch_news([], 1)
                for a in articles:
                    if callback:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(a)
                        else:
                            callback(a)
                
                # Wait for next interval + jitter
                wait_time = random.uniform(self.intervals[0], self.intervals[1])
                await asyncio.sleep(wait_time)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scraper loop error: {e}")
                await asyncio.sleep(60) # Backoff

    async def stop_stream(self) -> None:
        pass # Nothing to close for cloudscraper
