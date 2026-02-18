import asyncio
import logging
import hashlib
from datetime import datetime
from typing import List, Optional
from telethon import TelegramClient, events
from data_service.ai.sources.base_source import BaseNewsSource, Article

logger = logging.getLogger(__name__)

class TelegramSource(BaseNewsSource):
    def __init__(self, config: dict):
        super().__init__(config)
        self.api_id = config.get('api_id')
        self.api_hash = config.get('api_hash')
        self.phone = config.get('phone')
        self.channels = config.get('channels', [])
        self.keywords = config.get('keywords', ['XAU', 'GOLD', 'SILVER', 'XAG', 'USD', 'FED', 'CPI'])
        self.client: Optional[TelegramClient] = None
        self.stream_callback = None

    def get_source_name(self) -> str:
        return "Telegram"

    async def _init_client(self):
        if not self.client:
            # Session file will be created in current directory
            session_name = 'quantmuse_telegram_session'
            self.client = TelegramClient(session_name, self.api_id, self.api_hash)
            await self.client.start(phone=self.phone)

    async def fetch_news(self, symbols: List[str], hours_back: int) -> List[Article]:
        """
        Fetch historical messages from channels.
        Note: Telethon history fetching can be slow and rate-limited.
        """
        await self._init_client()
        articles = []
        
        # Simple implementation: fetch last N messages
        # Ideally we filter by date, but limit=100 is safer for rate limits
        for channel in self.channels:
            try:
                async for message in self.client.iter_messages(channel, limit=50):
                    if not message.text:
                        continue
                        
                    # Basic keyword filter
                    if not self._is_relevant(message.text, symbols):
                        continue
                        
                    article = self._message_to_article(message, channel)
                    articles.append(article)
            except Exception as e:
                logger.error(f"Error fetching history from {channel}: {e}")
                
        return articles

    async def start_stream(self, callback) -> None:
        """Start listening to new messages."""
        self.stream_callback = callback
        await self._init_client()
        
        @self.client.on(events.NewMessage(chats=self.channels))
        async def handler(event):
            try:
                if not event.text:
                    return
                
                # Check global keywords (defined in init) 
                # In a real system, we might want dynamic symbol checking
                if not self._is_relevant(event.text, self.keywords):
                    return

                article = self._message_to_article(event.message, event.chat.username or str(event.chat_id))
                
                if self.stream_callback:
                    if asyncio.iscoroutinefunction(self.stream_callback):
                        await self.stream_callback(article)
                    else:
                        self.stream_callback(article)
                        
            except Exception as e:
                logger.error(f"Error processing Telegram update: {e}")

        logger.info(f"Telegram listener started for channels: {self.channels}")
        # Keep client running? accessing client properties keeps it alive usually if attached to loop
        # But for script usage we might need run_until_disconnected in main loop

    async def stop_stream(self) -> None:
        if self.client:
            await self.client.disconnect()

    def _is_relevant(self, text: str, keywords: List[str]) -> bool:
        text_upper = text.upper()
        for kw in keywords:
            if kw.upper() in text_upper:
                return True
        return False

    def _message_to_article(self, message, channel_name: str) -> Article:
        # Generate ID
        msg_id = str(message.id)
        ts = message.date.timestamp()
        
        # Determine main symbol (naive approach)
        symbol = "UNKNOWN"
        for kw in self.keywords:
            if kw in message.text.upper():
                symbol = kw
                break
                
        return Article(
            id=hashlib.md5(f"{channel_name}_{msg_id}".encode()).hexdigest(),
            symbol=symbol,
            title=message.text[:100].replace('\n', ' ') + "...", # First 100 chars as title
            content=message.text,
            source=f"Telegram ({channel_name})",
            published_at=message.date,
            url=f"https://t.me/{channel_name}/{msg_id}" if isinstance(channel_name, str) else None,
            raw_data={"msg_id": msg_id, "channel": channel_name}
        )
