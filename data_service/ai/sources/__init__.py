from .base_source import BaseNewsSource, Article

# Optional imports - only load if dependencies available
try:
    from .telegram_source import TelegramSource
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False
    TelegramSource = None

try:
    from .investing_com_source import InvestingComSource
    _INVESTING_AVAILABLE = True
except ImportError:
    _INVESTING_AVAILABLE = False
    InvestingComSource = None

from .google_rss_source import GoogleRSSSource
from .rss_multi_source import RSSMultiSource
from .ddg_source import DDGNewsSource
from .mock_source import MockNewsSource


